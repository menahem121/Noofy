from app.engine.models import MissingModel, WorkflowValidationResult
from app.workflows.package import WorkflowPackage


class WorkflowPackageValidator:
    def validate_structure(self, package: WorkflowPackage) -> WorkflowValidationResult:
        errors: list[str] = []

        graph_node_ids = set(package.comfyui_graph.keys())
        input_ids = {workflow_input.id for workflow_input in package.inputs}

        for workflow_input in package.inputs:
            if workflow_input.binding.node_id not in graph_node_ids:
                errors.append(
                    f"Input '{workflow_input.id}' references missing node '{workflow_input.binding.node_id}'."
                )

        for section in package.dashboard.sections:
            for control in section.controls:
                if control.input_id and control.input_id not in input_ids:
                    errors.append(f"Dashboard control '{control.id}' references missing input '{control.input_id}'.")

        return WorkflowValidationResult(
            workflow_id=package.metadata.id,
            valid=not errors,
            errors=errors,
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
        )
