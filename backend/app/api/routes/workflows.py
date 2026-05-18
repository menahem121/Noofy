from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import (
    DashboardAssetServiceDep,
    DashboardAuthoringServiceDep,
    RunOrchestratorDep,
    RunJobServiceDep,
    UserStateServiceDep,
    WorkflowExporterDep,
    WorkflowImportOrchestratorDep,
    WorkflowRunnerLifecycleServiceDep,
    WorkflowLibraryServiceDep,
)
from app.engine.models import WorkflowRunRequest
from app.runs.credentials import credential_input_ids
from app.workflows.import_orchestrator import (
    DuplicateWorkflowIdentityError,
    ImportSessionExpiredError,
)
from app.workflows.assets import AssetUploadError
from app.workflows.authoring import DashboardAuthoringError
from app.workflows.exporter import WorkflowExportError
from app.workflows.importer import NoofyImportError
from app.workflows.library import WorkflowMetadataUpdate
from app.workflows.user_state import WorkflowUserState

router = APIRouter()


class WorkflowExportRequest(BaseModel):
    input_values: dict[str, Any] | None = Field(default=None)
    export_metadata: dict[str, Any] | None = Field(default=None)


class WorkflowImportCommitRequest(BaseModel):
    duplicate_action: str | None = Field(default=None)


# ─── Workflow library ────────────────────────────────────────────────────────

@router.get("/workflows")
async def list_workflows(library: WorkflowLibraryServiceDep) -> list[dict[str, object]]:
    return library.list_workflows()


@router.get("/workflows/{workflow_id}/package")
async def get_workflow_package(workflow_id: str, library: WorkflowLibraryServiceDep):
    try:
        return library.workflow_package_payload(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/details")
async def get_workflow_details(workflow_id: str, library: WorkflowLibraryServiceDep):
    try:
        return library.workflow_details(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/workflows/{workflow_id}/metadata")
async def update_workflow_metadata(
    workflow_id: str,
    request: WorkflowMetadataUpdate,
    library: WorkflowLibraryServiceDep,
):
    try:
        return library.update_workflow_metadata(workflow_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}")
async def remove_workflow(workflow_id: str, library: WorkflowLibraryServiceDep):
    try:
        return library.remove_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/model-summary")
async def get_workflow_model_summary(workflow_id: str, library: WorkflowLibraryServiceDep):
    try:
        return library.model_availability_summary(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/model-verification")
async def start_workflow_model_verification(workflow_id: str, library: WorkflowLibraryServiceDep):
    try:
        return library.start_model_verification(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/model-verification/{job_id}")
async def get_workflow_model_verification_status(
    workflow_id: str,
    job_id: str,
    library: WorkflowLibraryServiceDep,
):
    try:
        return library.model_verification_status(workflow_id, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/install-state")
async def get_workflow_install_state(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return runner_lifecycle.get_install_state(workflow_id)


@router.get("/workflows/{workflow_id}/install-state/developer-details")
async def get_workflow_install_state_developer_details(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return runner_lifecycle.get_install_state_developer_details(workflow_id)


@router.get("/workflows/{workflow_id}/status")
async def get_workflow_status(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    try:
        return runner_lifecycle.workflow_status(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ─── Import ──────────────────────────────────────────────────────────────────

@router.post("/workflows/import")
async def import_workflow(
    request: Request,
    import_orchestrator: WorkflowImportOrchestratorDep,
    filename: str | None = None,
    allow_unverified_community_preparation: bool = False,
):
    try:
        return import_orchestrator.import_workflow_archive(
            await request.body(),
            original_filename=filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflows/import/preview")
async def preview_workflow_import(
    request: Request,
    import_orchestrator: WorkflowImportOrchestratorDep,
    filename: str | None = None,
    allow_unverified_community_preparation: bool = False,
):
    try:
        return import_orchestrator.preview_workflow_import(
            await request.body(),
            original_filename=filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/download-models")
async def download_import_missing_models(
    import_session_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
):
    try:
        return import_orchestrator.start_missing_model_download_for_import(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/import/{import_session_id}/download-models/{job_id}")
async def get_import_model_download_status(
    import_session_id: str,
    job_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
):
    try:
        return import_orchestrator.import_model_download_status(import_session_id, job_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/import/{import_session_id}/model-verification")
async def get_import_model_verification_status(
    import_session_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
):
    try:
        return import_orchestrator.import_model_verification_status(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/download-models/{job_id}/cancel")
async def cancel_import_model_download(
    import_session_id: str,
    job_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
):
    try:
        return import_orchestrator.cancel_import_model_download_job(import_session_id, job_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/commit")
async def commit_workflow_import(
    import_session_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
    request: WorkflowImportCommitRequest | None = None,
):
    try:
        if request is None:
            return import_orchestrator.commit_workflow_import(import_session_id)
        return import_orchestrator.commit_workflow_import(
            import_session_id, duplicate_action=request.duplicate_action
        )
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateWorkflowIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/workflows/import/{import_session_id}")
async def cancel_workflow_import(
    import_session_id: str,
    import_orchestrator: WorkflowImportOrchestratorDep,
):
    try:
        return import_orchestrator.cancel_workflow_import(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc


# ─── Runner lifecycle ────────────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/prepare")
async def prepare_workflow(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return await runner_lifecycle.prepare_workflow(workflow_id)


@router.delete("/workflows/{workflow_id}/prepare")
async def cancel_workflow_preparation(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    try:
        return runner_lifecycle.cancel_preparation(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/runner/start")
async def start_workflow_runner(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return await runner_lifecycle.start_workflow_runner(workflow_id)


@router.delete("/workflows/runner/queue/{queue_id}")
async def cancel_queued_runner_start(
    queue_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return runner_lifecycle.cancel_queued_runner_start(queue_id)


@router.post("/workflows/{workflow_id}/runner/stop")
async def stop_workflow_runner(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    return await runner_lifecycle.stop_workflow_runner(workflow_id)


@router.post("/workflows/{workflow_id}/runner/leases")
async def open_workflow_runner_lease(
    workflow_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    try:
        return runner_lifecycle.open_workflow_runner_lease(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}/runner/leases/{lease_id}")
async def close_workflow_runner_lease(
    workflow_id: str,
    lease_id: str,
    runner_lifecycle: WorkflowRunnerLifecycleServiceDep,
):
    try:
        return runner_lifecycle.close_workflow_runner_lease(workflow_id, lease_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ─── Run ─────────────────────────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str, orchestrator: RunOrchestratorDep):
    try:
        return await orchestrator.validate_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    request: WorkflowRunRequest,
    orchestrator: RunOrchestratorDep,
):
    try:
        return await orchestrator.run_workflow(
            workflow_id,
            request.inputs,
            request.options,
            output_preferences_snapshot=request.output_preferences_snapshot,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Dashboard authoring ─────────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/bindable-inputs")
async def get_bindable_inputs(workflow_id: str, authoring: DashboardAuthoringServiceDep):
    try:
        return authoring.get_bindable_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardAuthoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/unresolved-inputs")
async def get_unresolved_inputs(workflow_id: str, authoring: DashboardAuthoringServiceDep):
    try:
        return authoring.get_unresolved_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardAuthoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/dashboard/validate")
async def validate_dashboard(
    workflow_id: str,
    request: Request,
    authoring: DashboardAuthoringServiceDep,
):
    try:
        body = await request.json()
        return authoring.validate_dashboard(
            workflow_id,
            inputs=body.get("inputs", []),
            dashboard=body.get("dashboard", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardAuthoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/workflows/{workflow_id}/dashboard")
async def save_dashboard(
    workflow_id: str,
    request: Request,
    authoring: DashboardAuthoringServiceDep,
):
    try:
        body = await request.json()
        return authoring.save_dashboard(
            workflow_id,
            inputs=body.get("inputs", []),
            dashboard=body.get("dashboard", {}),
        )
    except (KeyError, DashboardAuthoringError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}/dashboard")
async def reset_dashboard_override(
    workflow_id: str,
    authoring: DashboardAuthoringServiceDep,
):
    try:
        return authoring.reset_dashboard_override(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardAuthoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Export ──────────────────────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/export")
async def export_workflow(workflow_id: str, exporter: WorkflowExporterDep):
    return _workflow_archive_response(workflow_id, exporter, input_values=None)


@router.post("/workflows/{workflow_id}/export")
async def export_workflow_with_values(
    workflow_id: str,
    request: WorkflowExportRequest,
    exporter: WorkflowExporterDep,
):
    return _workflow_archive_response(
        workflow_id,
        exporter,
        input_values=request.input_values,
        export_metadata=request.export_metadata,
    )


def _workflow_archive_response(
    workflow_id: str,
    exporter,
    *,
    input_values: dict[str, Any] | None,
    export_metadata: dict[str, Any] | None = None,
):
    try:
        if input_values is None and export_metadata is None:
            archive_bytes, filename = exporter.export_archive(workflow_id)
        else:
            archive_bytes, filename = exporter.export_archive(
                workflow_id,
                input_values=input_values,
                export_metadata=export_metadata,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WorkflowExportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from fastapi.responses import Response
    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/workflows/{workflow_id}/export/comfyui-json")
async def export_workflow_comfyui_json(workflow_id: str, exporter: WorkflowExporterDep):
    return _workflow_comfyui_json_response(workflow_id, exporter, input_values=None)


@router.post("/workflows/{workflow_id}/export/comfyui-json")
async def export_workflow_comfyui_json_with_values(
    workflow_id: str,
    request: WorkflowExportRequest,
    exporter: WorkflowExporterDep,
):
    return _workflow_comfyui_json_response(
        workflow_id,
        exporter,
        input_values=request.input_values,
    )


def _workflow_comfyui_json_response(
    workflow_id: str,
    exporter,
    *,
    input_values: dict[str, Any] | None,
):
    try:
        if input_values is None:
            graph_bytes, filename = exporter.export_comfyui_graph(workflow_id)
        else:
            graph_bytes, filename = exporter.export_comfyui_graph(
                workflow_id,
                input_values=input_values,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WorkflowExportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from fastapi.responses import Response
    return Response(
        content=graph_bytes,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/workflows/{workflow_id}/uploads/image")
async def upload_workflow_image(
    workflow_id: str,
    job_service: RunJobServiceDep,
    image: UploadFile = File(...),
):
    try:
        data = await image.read()
        return await job_service.upload_workflow_image(
            workflow_id,
            filename=image.filename or "upload.png",
            data=data,
            content_type=image.content_type or "image/png",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── User state ──────────────────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/user-state")
async def get_user_state(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.get(workflow_id)


@router.put("/workflows/{workflow_id}/user-state")
async def save_user_state(
    workflow_id: str,
    request: Request,
    user_state_service: UserStateServiceDep,
    library: WorkflowLibraryServiceDep,
):
    body = await request.json()
    body["workflow_id"] = workflow_id
    try:
        state = WorkflowUserState.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        package = library.workflow_loader.get_package(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return user_state_service.save(
        state,
        credential_input_ids=credential_input_ids(package),
    )


@router.delete("/workflows/{workflow_id}/user-state/values")
async def clear_user_state_values(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.clear_values(workflow_id)


@router.delete("/workflows/{workflow_id}/user-state/layout")
async def clear_user_state_layout(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.clear_layout(workflow_id)


# ─── Dashboard assets ────────────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/assets/image")
async def upload_dashboard_asset(
    workflow_id: str,
    asset_service: DashboardAssetServiceDep,
    image: UploadFile = File(...),
):
    data = await image.read()
    content_type = image.content_type or "application/octet-stream"
    original_filename = image.filename or "upload"
    try:
        return asset_service.store(data, content_type, original_filename)
    except AssetUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
