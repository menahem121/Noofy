from fastapi import APIRouter, HTTPException

from app.api.deps import ApiKeyServiceDep, EngineServiceDep, ModelFolderServiceDep
from app.api.schemas import ApiKeyUpdateRequest, ModelFolderUpdateRequest
from app.settings.api_keys import CredentialStoreUnavailable, provider_from_slug

router = APIRouter()


@router.get("/settings/apis")
async def api_key_settings(api_key_service: ApiKeyServiceDep):
    return api_key_service.settings()


@router.get("/settings/model-folders")
async def model_folder_settings(model_folder_service: ModelFolderServiceDep):
    return model_folder_service.settings()


@router.put("/settings/model-folders")
async def update_model_folder_settings(
    request: ModelFolderUpdateRequest,
    model_folder_service: ModelFolderServiceDep,
):
    try:
        return model_folder_service.update(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/apis/{provider}/key")
async def save_api_key(
    provider: str,
    request: ApiKeyUpdateRequest,
    api_key_service: ApiKeyServiceDep,
):
    resolved_provider = provider_from_slug(provider)
    if resolved_provider is None:
        raise HTTPException(status_code=404, detail="Unknown API key provider.")
    try:
        return api_key_service.save_key(resolved_provider, request.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/settings/apis/{provider}/key")
async def clear_api_key(
    provider: str,
    api_key_service: ApiKeyServiceDep,
):
    resolved_provider = provider_from_slug(provider)
    if resolved_provider is None:
        raise HTTPException(status_code=404, detail="Unknown API key provider.")
    try:
        return api_key_service.clear_key(resolved_provider)
    except CredentialStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/trust/policy")
async def trust_policy(engine_service: EngineServiceDep):
    return engine_service.trust_policy_payload()
