from __future__ import annotations

from collections.abc import Callable

from app.engine.models import RuntimeBootstrapResult
from app.runtime.comfyui_updates import (
    ComfyUIRebuildRequest,
    ComfyUIUpdateRequest,
    ComfyUIUpdateService,
)
from app.runtime.launch_settings import (
    ComfyUILaunchSettings,
    ComfyUILaunchSettingsResponse,
    ComfyUILaunchSettingsStore,
    ComfyUILaunchSettingsUpdateResult,
    comfyui_launch_response,
)
from app.runtime.manager import RuntimeManager


class ComfyUISidecarService:
    """Owns ComfyUI sidecar lifecycle and runtime maintenance operations."""

    def __init__(
        self,
        *,
        runtime_manager: RuntimeManager,
        update_service: ComfyUIUpdateService | None = None,
        launch_settings_store: ComfyUILaunchSettingsStore | None = None,
        on_endpoint_changed: Callable[[], None] | None = None,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.update_service = update_service
        self.launch_settings_store = launch_settings_store
        self.on_endpoint_changed = on_endpoint_changed

    async def status(self):
        return await self.runtime_manager.status()

    async def runtime_status(self):
        return await self.status()

    def launch_settings(self) -> ComfyUILaunchSettingsResponse:
        launch_settings = self._read_launch_settings()
        return comfyui_launch_response(launch_settings, mode=self.runtime_manager.mode)

    def comfyui_launch_settings(self) -> ComfyUILaunchSettingsResponse:
        return self.launch_settings()

    async def update_launch_settings(
        self,
        request: ComfyUILaunchSettings,
    ) -> ComfyUILaunchSettingsUpdateResult:
        current = self._read_launch_settings()
        response = comfyui_launch_response(request, mode=self.runtime_manager.mode)

        if self.runtime_manager.mode != "managed":
            return ComfyUILaunchSettingsUpdateResult(
                status="blocked",
                settings=response,
                error=response.disabled_reason,
            )

        changed = current.vram_mode != request.vram_mode
        self._write_launch_settings(request)
        self.runtime_manager.set_managed_vram_mode(request.vram_mode)

        if not changed:
            return ComfyUILaunchSettingsUpdateResult(status="unchanged", settings=response)

        if not self.runtime_manager.is_managed_process_running():
            return ComfyUILaunchSettingsUpdateResult(status="updated", settings=response)

        await self.runtime_manager.stop()
        start_result = await self.start()
        restart_ok = start_result.status in {
            "started",
            "already_running",
            "repair_completed_started",
        }
        return ComfyUILaunchSettingsUpdateResult(
            status="updated_restarted" if restart_ok else "updated_restart_failed",
            settings=response,
            restart_status=start_result.status,
            error=None if restart_ok else start_result.comfyui.error,
        )

    async def update_comfyui_launch_settings(
        self,
        request: ComfyUILaunchSettings,
    ) -> ComfyUILaunchSettingsUpdateResult:
        return await self.update_launch_settings(request)

    async def start(self):
        result = await self.runtime_manager.start()
        if (
            self.update_service is not None
            and result.status in {"environment_not_ready", "repo_missing", "startup_failed"}
        ):
            result = await self.update_service.repair_after_start_failure(
                result,
                repair_reason=result.comfyui.error or result.status,
            )
        if self.on_endpoint_changed is not None:
            self.on_endpoint_changed()
        return result

    async def start_comfyui(self):
        return await self.start()

    async def stop(self):
        return await self.runtime_manager.stop()

    async def stop_comfyui(self):
        return await self.stop()

    async def bootstrap_runtime(self) -> RuntimeBootstrapResult:
        return await self.runtime_manager.bootstrap_environment()

    async def bootstrap_comfyui_runtime(self) -> RuntimeBootstrapResult:
        return await self.bootstrap_runtime()

    async def versions(self, *, check_upstream: bool = False):
        if self.update_service is None:
            return {
                "updates_allowed": False,
                "disabled_reason": "ComfyUI updater is not configured.",
                "upstream_checked": False,
                "options": [],
            }
        return await self.update_service.versions(check_upstream=check_upstream)

    async def comfyui_versions(self, *, check_upstream: bool = False):
        return await self.versions(check_upstream=check_upstream)

    async def update(self, request: ComfyUIUpdateRequest):
        if self.update_service is None:
            return {
                "status": "blocked",
                "phase": "blocked",
                "error": "ComfyUI updater is not configured.",
            }
        return await self.update_service.start_update(request)

    async def update_comfyui(self, request: ComfyUIUpdateRequest):
        return await self.update(request)

    async def rebuild(self, request: ComfyUIRebuildRequest):
        if self.update_service is None:
            return {
                "operation": "rebuild",
                "status": "blocked",
                "phase": "blocked",
                "error": "ComfyUI updater is not configured.",
            }
        return await self.update_service.start_rebuild(request)

    async def rebuild_comfyui(self, request: ComfyUIRebuildRequest):
        return await self.rebuild(request)

    def update_status(self):
        if self.update_service is None:
            return {
                "status": "idle",
                "phase": "idle",
                "error": "ComfyUI updater is not configured.",
            }
        return self.update_service.update_status()

    def comfyui_update_status(self):
        return self.update_status()

    async def shutdown(self) -> None:
        if self.runtime_manager.mode == "managed":
            await self.runtime_manager.stop()

    def _read_launch_settings(self) -> ComfyUILaunchSettings:
        if self.launch_settings_store is None:
            return ComfyUILaunchSettings()
        return self.launch_settings_store.read()

    def _write_launch_settings(self, launch_settings: ComfyUILaunchSettings) -> None:
        if self.launch_settings_store is not None:
            self.launch_settings_store.write(launch_settings)
