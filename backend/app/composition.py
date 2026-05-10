from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
    return create_api_services(engine_service=create_default_engine_service())


def create_api_services(
    *,
    engine_service: EngineService,
    user_state_service: UserStateService | None = None,
    asset_service: DashboardAssetService | None = None,
) -> ApiServices:
    return ApiServices(
        engine_service=engine_service,
        user_state_service=user_state_service or UserStateService(settings.paths.user_state_dir),
        asset_service=asset_service or DashboardAssetService(settings.paths.dashboard_assets_dir),
    )


ApiServicesFactory = Callable[[], ApiServices]
