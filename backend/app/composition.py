from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.engine.factory import create_default_engine_service
from app.engine.service import EngineService
from app.workflows.assets import DashboardAssetService
from app.workflows.user_state import UserStateService


@dataclass(frozen=True)
class ApiServices:
    engine_service: EngineService
    user_state_service: UserStateService
    asset_service: DashboardAssetService


def create_default_api_services() -> ApiServices:
    return ApiServices(
        engine_service=create_default_engine_service(),
        user_state_service=UserStateService(settings.paths.user_state_dir),
        asset_service=DashboardAssetService(settings.paths.dashboard_assets_dir),
    )


default_api_services = create_default_api_services()
