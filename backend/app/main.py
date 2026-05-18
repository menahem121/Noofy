import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import router as _api_router
from app.composition import (
    ApiServices,
    ApiServicesFactory,
    create_api_services,
    create_default_api_services,
)
from app.core.auth import LocalApiTokenMiddleware
from app.core.config import settings
from app.diagnostics import sanitize
from app.engine.service import EngineService
from app.gallery import GalleryStore
from app.models.downloads import ModelDownloadJobService
from app.models.ownership import ModelOwnershipStore
from app.models.tags import ModelTagStore
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.settings.api_keys import ApiKeySettingsService
from app.settings.onboarding import OnboardingSettingsService
from app.models.folders import ModelFolderSettingsService
from app.workflows.assets import DashboardAssetService
from app.workflows.user_state import UserStateService

logger = logging.getLogger(__name__)


async def _start_comfyui_background(sidecar_service: ComfyUISidecarService) -> None:
    try:
        result = await sidecar_service.start_comfyui()
        sidecar_service.runtime_manager.log_store.add(
            "info",
            "Managed ComfyUI background startup finished",
            "app.lifespan",
            details={"status": result.status},
        )
        logger.info("Managed ComfyUI startup: status=%s", result.status)
    except Exception as exc:
        sidecar_service.runtime_manager.log_store.add(
            "error",
            "Managed ComfyUI failed to start during backend startup",
            "app.lifespan",
            details={"error": str(exc), "error_type": type(exc).__name__},
        )
        logger.exception("Managed ComfyUI failed to start during backend startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = _api_services_for_app(app)
    startup_task: asyncio.Task[None] | None = None
    if settings.comfyui_runtime_mode == "managed":
        startup_task = asyncio.create_task(
            _start_comfyui_background(services.comfyui_sidecar_service)
        )
    try:
        yield
    finally:
        if startup_task is not None and not startup_task.done():
            startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup_task
        await services.engine_service.shutdown()


def create_app(
    *,
    services: ApiServices | None = None,
    engine_service: EngineService | None = None,
    comfyui_sidecar_service: ComfyUISidecarService | None = None,
    user_state_service: UserStateService | None = None,
    asset_service: DashboardAssetService | None = None,
    gallery_store: GalleryStore | None = None,
    api_key_service: ApiKeySettingsService | None = None,
    onboarding_service: OnboardingSettingsService | None = None,
    model_folder_service: ModelFolderSettingsService | None = None,
    model_tag_store: ModelTagStore | None = None,
    model_ownership_store: ModelOwnershipStore | None = None,
    model_download_service: ModelDownloadJobService | None = None,
    service_factory: ApiServicesFactory = create_default_api_services,
) -> FastAPI:
    if services is not None and any(
        item is not None
        for item in (
            engine_service,
            comfyui_sidecar_service,
            user_state_service,
            asset_service,
            gallery_store,
            api_key_service,
            onboarding_service,
            model_folder_service,
            model_tag_store,
            model_ownership_store,
            model_download_service,
        )
    ):
        raise ValueError("Pass either services or individual service overrides, not both.")
    if services is None and any(
        item is not None
        for item in (
            engine_service,
            comfyui_sidecar_service,
            user_state_service,
            asset_service,
            gallery_store,
            api_key_service,
            onboarding_service,
            model_folder_service,
            model_tag_store,
            model_ownership_store,
            model_download_service,
        )
    ):
        if engine_service is None:
            raise ValueError("engine_service is required when overriding API services.")
        services = create_api_services(
            engine_service=engine_service,
            comfyui_sidecar_service=comfyui_sidecar_service,
            user_state_service=user_state_service,
            asset_service=asset_service,
            gallery_store=gallery_store,
            api_key_service=api_key_service,
            onboarding_service=onboarding_service,
            model_folder_service=model_folder_service,
            model_tag_store=model_tag_store,
            model_ownership_store=model_ownership_store,
            model_download_service=model_download_service,
        )

    app = FastAPI(title="Local AI Workflow Backend", version="0.1.0", lifespan=lifespan)
    app.state.api_services = services
    app.state.api_service_factory = service_factory
    app.add_middleware(LocalApiTokenMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.noofy_cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
    )
    app.add_exception_handler(StarletteHTTPException, _sanitized_http_exception_handler)
    app.add_exception_handler(RequestValidationError, _sanitized_request_validation_exception_handler)
    app.include_router(_api_router, prefix="/api")
    return app


async def _sanitized_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    del request
    return JSONResponse(
        {"detail": sanitize(exc.detail)},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _sanitized_request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "detail": "Request validation failed.",
            "errors": [
                {
                    "loc": error.get("loc", ()),
                    "msg": sanitize(error.get("msg", "Invalid request.")),
                    "type": error.get("type", "validation_error"),
                }
                for error in exc.errors()
            ],
        },
        status_code=422,
    )


def _api_services_for_app(app: FastAPI) -> ApiServices:
    services = getattr(app.state, "api_services", None)
    if services is None:
        factory: ApiServicesFactory = app.state.api_service_factory
        services = factory()
        app.state.api_services = services
    return services


app = create_app()
