import math

from app.engine.models import MissingModel, WorkflowValidationResult
from app.workflows.package import (
    DASHBOARD_CONTROL_TYPES,
    WORKFLOW_OUTPUT_KINDS,
    WorkflowInput,
    WorkflowPackage,
)

_GRID_COLUMNS = 32


def _layouts_overlap(a: dict, b: dict) -> bool:
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["w"], by1 + b["h"]
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _validate_layout(kind: str, item_id: str, layout: object, errors: list[str]) -> dict | None:
    x = getattr(layout, "x")
    y = getattr(layout, "y")
    w = getattr(layout, "w")
    h = getattr(layout, "h")
    min_w = getattr(layout, "min_w", None)
    min_h = getattr(layout, "min_h", None)
    valid = True

    if x < 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.x must be greater than or equal to 0.")
        valid = False
    if y < 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.y must be greater than or equal to 0.")
        valid = False
    if w <= 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.w must be greater than 0.")
        valid = False
    if h <= 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.h must be greater than 0.")
        valid = False
    if min_w is not None and min_w <= 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.min_w must be greater than 0.")
        valid = False
    if min_h is not None and min_h <= 0:
        errors.append(f"Dashboard {kind} '{item_id}' layout.min_h must be greater than 0.")
        valid = False
    if min_w is not None and w > 0 and min_w > w:
        errors.append(f"Dashboard {kind} '{item_id}' layout.min_w must not be larger than layout.w.")
        valid = False
    if min_h is not None and h > 0 and min_h > h:
        errors.append(f"Dashboard {kind} '{item_id}' layout.min_h must not be larger than layout.h.")
        valid = False
    if w > _GRID_COLUMNS:
        errors.append(f"Dashboard {kind} '{item_id}' layout.w must not exceed the {_GRID_COLUMNS}-column grid.")
        valid = False
    if x >= 0 and w > 0 and x + w > _GRID_COLUMNS:
        errors.append(
            f"Dashboard {kind} '{item_id}' extends beyond the {_GRID_COLUMNS}-column grid."
        )
        valid = False

    if not valid:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _validate_slider_input(workflow_input: WorkflowInput, errors: list[str]) -> None:
    validation = workflow_input.validation
    constrained = any(field in validation for field in ("min", "max", "step"))
    if not constrained:
        return

    values: dict[str, float] = {}
    for field in ("min", "max", "step"):
        if field not in validation:
            continue
        numeric = _finite_number(validation[field])
        if numeric is None:
            errors.append(
                f"Slider input '{workflow_input.id}' has a non-numeric validation.{field}."
            )
        else:
            values[field] = numeric

    default = _finite_number(workflow_input.default)
    if default is None:
        errors.append(f"Slider input '{workflow_input.id}' must have a numeric default.")

    step = values.get("step")
    if step is not None and step <= 0:
        errors.append(f"Slider input '{workflow_input.id}' must have validation.step greater than 0.")

    minimum = values.get("min")
    maximum = values.get("max")
    if minimum is not None and maximum is not None and maximum <= minimum:
        errors.append(
            f"Slider input '{workflow_input.id}' must have validation.max greater than validation.min."
        )
        return

    if default is not None:
        if minimum is not None and default < minimum:
            errors.append(f"Slider input '{workflow_input.id}' has a default below validation.min.")
        if maximum is not None and default > maximum:
            errors.append(f"Slider input '{workflow_input.id}' has a default above validation.max.")

    if minimum is None or step is None or step <= 0:
        return
    for field, numeric in (("max", maximum), ("default", default)):
        if numeric is None:
            continue
        step_index = (numeric - minimum) / step
        if not math.isclose(step_index, round(step_index), rel_tol=0.0, abs_tol=1e-7):
            errors.append(
                f"Slider input '{workflow_input.id}' has a {field} that does not align with validation.step from validation.min."
            )


class WorkflowPackageValidator:
    def validate_structure(self, package: WorkflowPackage) -> WorkflowValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        graph_node_ids = set(package.comfyui_graph.keys())
        input_ids = {workflow_input.id for workflow_input in package.inputs}
        output_ids = {workflow_output.id for workflow_output in package.outputs}
        outputs_by_id = {workflow_output.id: workflow_output for workflow_output in package.outputs}

        # Validate input bindings reference graph nodes.
        for workflow_input in package.inputs:
            if not workflow_input.binding.input_name:
                errors.append(f"Input '{workflow_input.id}' has an empty binding.input_name.")
            if workflow_input.binding.node_id not in graph_node_ids:
                errors.append(
                    f"Input '{workflow_input.id}' references missing node '{workflow_input.binding.node_id}'."
                )
                continue
            node = package.comfyui_graph.get(workflow_input.binding.node_id)
            node_inputs = node.get("inputs") if isinstance(node, dict) else None
            if isinstance(node_inputs, dict) and workflow_input.binding.input_name not in node_inputs:
                errors.append(
                    f"Input '{workflow_input.id}' references missing input '{workflow_input.binding.input_name}' on node '{workflow_input.binding.node_id}'."
                )
            if workflow_input.control == "load_file":
                accepted_extensions = workflow_input.validation.get("accepted_extensions")
                accepted_mime_types = workflow_input.validation.get("accepted_mime_types")
                has_extensions = isinstance(accepted_extensions, list) and any(
                    isinstance(item, str) and item.strip() for item in accepted_extensions
                )
                has_mime_types = isinstance(accepted_mime_types, list) and any(
                    isinstance(item, str) and item.strip() for item in accepted_mime_types
                )
                if not has_extensions and not has_mime_types:
                    errors.append(
                        f"Input '{workflow_input.id}' is control 'load_file' but has no accepted_extensions or accepted_mime_types validation."
                    )
            if workflow_input.control == "slider":
                _validate_slider_input(workflow_input, errors)

        for workflow_output in package.outputs:
            if workflow_output.node_id not in graph_node_ids:
                errors.append(
                    f"Output '{workflow_output.id}' references missing node '{workflow_output.node_id}'."
                )
            output_kind = workflow_output.kind or workflow_output.type
            if output_kind not in WORKFLOW_OUTPUT_KINDS:
                errors.append(
                    f"Output '{workflow_output.id}' declares unsupported kind '{output_kind}'."
                )

        # Validate dashboard controls.
        seen_control_ids: set[str] = set()
        seen_group_ids: set[str] = set()
        seen_grouped_control_ids: set[str] = set()
        layouts_with_id: list[tuple[str, dict]] = []
        input_ids_referenced: set[str] = set()
        if package.dashboard.status == "configured" and not any(
            section.controls for section in package.dashboard.sections
        ):
            errors.append("Configured dashboard has no controls.")

        for section in package.dashboard.sections:
            section_control_ids = {control.id for control in section.controls}
            section_grouped_control_ids: set[str] = set()
            for control in section.controls:
                # Duplicate control id check.
                if control.id in seen_control_ids:
                    errors.append(f"Duplicate dashboard control id '{control.id}'.")
                seen_control_ids.add(control.id)
                if control.type not in DASHBOARD_CONTROL_TYPES:
                    errors.append(
                        f"Dashboard control '{control.id}' uses unsupported type '{control.type}'."
                    )

                # input_id must reference a known input.
                if control.type == "api_credential":
                    if control.input_id and control.input_id not in input_ids:
                        # Credential refs are allowed to be control-owned, so
                        # an input_id is optional and not graph-bound.
                        pass
                    strategy = control.injection_strategy
                    if control.provider != "comfy_org":
                        errors.append(f"Dashboard control '{control.id}' uses an unsupported credential provider.")
                    if control.secret_ref != "api-key:comfy_org":
                        errors.append(f"Dashboard control '{control.id}' has an invalid credential reference.")
                    if strategy is None:
                        errors.append(f"Dashboard control '{control.id}' has no credential injection strategy.")
                    elif strategy.kind != "comfyui_extra_data" or strategy.field != "api_key_comfy_org":
                        errors.append(
                            f"Dashboard control '{control.id}' uses an unsupported credential injection strategy."
                        )
                elif control.input_id:
                    if control.input_id not in input_ids:
                        errors.append(
                            f"Dashboard control '{control.id}' references missing input '{control.input_id}'."
                        )
                    else:
                        input_ids_referenced.add(control.input_id)
                elif control.type not in {"display_image", "display_audio", "display_text", "display_video", "display_file", "display_3d", "result_image", "note"}:
                    errors.append(
                        f"Dashboard control '{control.id}' has no input_id."
                    )

                # Output media controls must reference a known output.
                if control.type in {"display_image", "display_audio", "display_text", "display_video", "display_file", "display_3d", "result_image"}:
                    if not control.output_id:
                        errors.append(
                            f"Dashboard control '{control.id}' is type '{control.type}' but has no output_id."
                        )
                    elif control.output_id not in output_ids:
                        errors.append(
                            f"Dashboard control '{control.id}' references missing output '{control.output_id}'."
                        )
                    else:
                        output = outputs_by_id[control.output_id]
                        output_kind = output.kind or output.type
                        if control.type == "display_audio" and output_kind != "audio":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_audio' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type == "display_text" and output_kind != "text":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_text' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type == "display_video" and output_kind != "video":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_video' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type == "display_file" and output_kind != "file":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_file' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type == "display_3d" and output_kind != "3d":
                            errors.append(
                                f"Dashboard control '{control.id}' is type '{control.type}' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type in {"display_image", "result_image"} and output_kind != "image":
                            errors.append(
                                f"Dashboard control '{control.id}' is type '{control.type}' but output '{output.id}' is '{output_kind}'."
                            )
                elif control.type == "note" and control.output_id:
                    errors.append(
                        f"Dashboard control '{control.id}' is type 'note' but must not have output_id."
                    )

            for group in section.groups:
                if group.id in seen_group_ids:
                    errors.append(f"Duplicate dashboard group id '{group.id}'.")
                seen_group_ids.add(group.id)

                if len(group.control_ids) < 2:
                    errors.append(f"Dashboard group '{group.id}' must contain at least two controls.")

                seen_group_control_ids: set[str] = set()
                for control_id in group.control_ids:
                    if control_id in seen_group_control_ids:
                        errors.append(
                            f"Dashboard group '{group.id}' references control '{control_id}' more than once."
                        )
                    seen_group_control_ids.add(control_id)
                    if control_id not in section_control_ids:
                        errors.append(
                            f"Dashboard group '{group.id}' references missing control '{control_id}'."
                        )
                    if control_id in seen_grouped_control_ids:
                        errors.append(
                            f"Dashboard control '{control_id}' is assigned to more than one group."
                        )
                    seen_grouped_control_ids.add(control_id)
                    if control_id in section_control_ids:
                        section_grouped_control_ids.add(control_id)

                if group.layout is not None:
                    layout_dict = _validate_layout("group", group.id, group.layout, errors)
                    if layout_dict is not None:
                        for other_id, other_layout in layouts_with_id:
                            if _layouts_overlap(layout_dict, other_layout):
                                errors.append(
                                    f"Dashboard layout item '{group.id}' overlaps '{other_id}'."
                                )
                        layouts_with_id.append((group.id, layout_dict))

            for control in section.controls:
                if control.id in section_grouped_control_ids:
                    continue
                # Layout overlap detection (responsive 32-column dashboard grid).
                if control.layout is not None:
                    layout_dict = _validate_layout("control", control.id, control.layout, errors)
                    if layout_dict is not None:
                        for other_id, other_layout in layouts_with_id:
                            if _layouts_overlap(layout_dict, other_layout):
                                errors.append(
                                    f"Dashboard layout item '{control.id}' overlaps '{other_id}'."
                                )
                        layouts_with_id.append((control.id, layout_dict))

        # Warn if an input has no corresponding control (not an error — input may be hidden/advanced).
        for input_id in input_ids:
            if input_id not in input_ids_referenced:
                warnings.append(f"Input '{input_id}' is not referenced by any dashboard control.")

        return WorkflowValidationResult(
            workflow_id=package.metadata.id,
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    def validate_models(self, package: WorkflowPackage, available_models: set[tuple[str, str]]) -> list[MissingModel]:
        missing: list[MissingModel] = []
        for model in package.required_models:
            if (model.folder, model.filename) not in available_models:
                missing.append(
                    MissingModel(
                        folder=model.folder,
                        filename=model.filename,
                        source_url=model.source_url,
                        checksum=model.checksum,
                        model_type=model.model_type,
                        verification_level=model.verification_level,
                        size_bytes=model.size_bytes,
                        source_urls=model.source_urls,
                    )
                )
        return missing

    def combine(
        self,
        package: WorkflowPackage,
        structure_result: WorkflowValidationResult,
        missing_models: list[MissingModel],
    ) -> WorkflowValidationResult:
        return WorkflowValidationResult(
            workflow_id=package.metadata.id,
            valid=structure_result.valid and not missing_models,
            missing_models=missing_models,
            errors=structure_result.errors,
            warnings=structure_result.warnings,
        )
