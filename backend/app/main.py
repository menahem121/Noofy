from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes
from app.core.auth import LocalApiTokenMiddleware
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
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
