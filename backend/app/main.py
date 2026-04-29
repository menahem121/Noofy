from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await routes.engine_service.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Local AI Workflow Backend", version="0.1.0", lifespan=lifespan)
    app.include_router(routes.router, prefix="/api")
    return app


app = create_app()
