"""Assembles all domain route sub-routers into a single APIRouter."""

from fastapi import APIRouter

from app.api.routes import (
    assets,
    comfyui,
    diagnostics,
    gallery,
    history,
    health,
    models,
    runners,
    runs,
    runtime,
    settings,
    workflows,
)

router = APIRouter()

router.include_router(health.router)
router.include_router(diagnostics.router)
router.include_router(gallery.router)
router.include_router(history.router)
router.include_router(settings.router)
router.include_router(runtime.router)
router.include_router(comfyui.router)
router.include_router(runners.router)
router.include_router(workflows.router)
router.include_router(runs.router)
router.include_router(models.router)
router.include_router(assets.router)
