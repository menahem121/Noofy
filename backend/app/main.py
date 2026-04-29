from fastapi import FastAPI

from app.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Local AI Workflow Backend", version="0.1.0")
    app.include_router(router, prefix="/api")
    return app


app = create_app()
