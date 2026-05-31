from app.engine.models import MissingModel, WorkflowValidationResult
from app.workflows.package import DASHBOARD_CONTROL_TYPES, WorkflowPackage

_GRID_COLUMNS = 32


def _layouts_overlap(a: dict, b: dict) -> bool:
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["w"], by1 + b["h"]
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


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

        for workflow_output in package.outputs:
            if workflow_output.node_id not in graph_node_ids:
                errors.append(
                    f"Output '{workflow_output.id}' references missing node '{workflow_output.node_id}'."
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
                elif control.type not in {"display_image", "display_audio", "display_video", "display_file", "result_image", "note"}:
                    errors.append(
                        f"Dashboard control '{control.id}' has no input_id."
                    )

                # Output media controls must reference a known output.
                if control.type in {"display_image", "display_audio", "display_video", "display_file", "result_image"}:
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
                        if control.type == "display_video" and output_kind != "video":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_video' but output '{output.id}' is '{output_kind}'."
                            )
                        if control.type == "display_file" and output_kind != "file":
                            errors.append(
                                f"Dashboard control '{control.id}' is type 'display_file' but output '{output.id}' is '{output_kind}'."
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
                    layout_dict = {
                        "x": group.layout.x,
                        "y": group.layout.y,
                        "w": group.layout.w,
                        "h": group.layout.h,
                    }
                    for other_id, other_layout in layouts_with_id:
                        if _layouts_overlap(layout_dict, other_layout):
                            errors.append(
                                f"Dashboard layout item '{group.id}' overlaps '{other_id}'."
                            )
                    layouts_with_id.append((group.id, layout_dict))

                    if group.layout.x + group.layout.w > _GRID_COLUMNS:
                        errors.append(
                            f"Dashboard group '{group.id}' extends beyond the {_GRID_COLUMNS}-column grid."
                        )

            for control in section.controls:
                if control.id in section_grouped_control_ids:
                    continue
                # Layout overlap detection (responsive 32-column dashboard grid).
                if control.layout is not None:
                    layout_dict = {
                        "x": control.layout.x,
                        "y": control.layout.y,
                        "w": control.layout.w,
                        "h": control.layout.h,
                    }
                    for other_id, other_layout in layouts_with_id:
                        if _layouts_overlap(layout_dict, other_layout):
                            errors.append(
                                f"Dashboard layout item '{control.id}' overlaps '{other_id}'."
                            )
                    layouts_with_id.append((control.id, layout_dict))

                    if control.layout.x + control.layout.w > _GRID_COLUMNS:
                        errors.append(
                            f"Dashboard control '{control.id}' extends beyond the {_GRID_COLUMNS}-column grid."
                        )

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
