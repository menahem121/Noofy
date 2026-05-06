import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes
from app.core.auth import LocalApiTokenMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)


async def _start_comfyui_background() -> None:
    try:
        result = await routes.engine_service.start_comfyui()
        logger.info("Managed ComfyUI startup: status=%s", result.status)
    except Exception:
        logger.exception("Managed ComfyUI failed to start during backend startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_task: asyncio.Task[None] | None = None
    if settings.comfyui_runtime_mode == "managed":
        startup_task = asyncio.create_task(_start_comfyui_background())
    try:
        yield
    finally:
        if startup_task is not None and not startup_task.done():
            startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await startup_task
        await routes.engine_service.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Local AI Workflow Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(LocalApiTokenMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.noofy_cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
    )
    app.include_router(routes.router, prefix="/api")
    return app


app = create_app()
