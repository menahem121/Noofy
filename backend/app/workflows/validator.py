from app.engine.models import MissingModel, WorkflowValidationResult
from app.workflows.package import WorkflowPackage

_GRID_COLUMNS = 12


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

        # Validate input bindings reference graph nodes.
        for workflow_input in package.inputs:
            if not workflow_input.binding.input_name:
                errors.append(f"Input '{workflow_input.id}' has an empty binding.input_name.")
            if workflow_input.binding.node_id not in graph_node_ids:
                errors.append(
                    f"Input '{workflow_input.id}' references missing node '{workflow_input.binding.node_id}'."
                )

        # Validate dashboard controls.
        seen_control_ids: set[str] = set()
        layouts_with_id: list[tuple[str, dict]] = []
        input_ids_referenced: set[str] = set()

        for section in package.dashboard.sections:
            for control in section.controls:
                # Duplicate control id check.
                if control.id in seen_control_ids:
                    errors.append(f"Duplicate dashboard control id '{control.id}'.")
                seen_control_ids.add(control.id)

                # input_id must reference a known input.
                if control.input_id:
                    if control.input_id not in input_ids:
                        errors.append(
                            f"Dashboard control '{control.id}' references missing input '{control.input_id}'."
                        )
                    else:
                        input_ids_referenced.add(control.input_id)

                # result_image control must reference a known output.
                if control.type == "result_image":
                    if not control.output_id:
                        errors.append(
                            f"Dashboard control '{control.id}' is type 'result_image' but has no output_id."
                        )
                    elif control.output_id not in output_ids:
                        errors.append(
                            f"Dashboard control '{control.id}' references missing output '{control.output_id}'."
                        )

                # Layout overlap detection (12-column grid).
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
                                f"Dashboard controls '{control.id}' and '{other_id}' have overlapping layouts."
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
