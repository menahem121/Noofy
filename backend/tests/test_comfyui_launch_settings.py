from pathlib import Path

import pytest

from app.engine.models import ComfyUIRuntimeStatus, ProcessActionResult
from app.runtime.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.launch_settings import ComfyUILaunchSettings, ComfyUILaunchSettingsStore


class FakeRuntimeManager:
    def __init__(self, *, mode: str = "managed", running: bool = False) -> None:
        self.mode = mode
        self.managed_vram_mode = "normal"
        self.base_url = "http://127.0.0.1:9000"
        self.ws_url = "ws://127.0.0.1:9000/ws"
        self._running = running
        self.stop_count = 0
        self.start_count = 0

    def set_managed_vram_mode(self, mode: str) -> None:
        self.managed_vram_mode = mode

    def is_managed_process_running(self) -> bool:
        return self._running

    async def status(self) -> ComfyUIRuntimeStatus:
        return ComfyUIRuntimeStatus(
            mode=self.mode,  # type: ignore[arg-type]
            reachable=self._running,
            base_url=self.base_url,
            repo_dir="/tmp/ComfyUI",
            managed_process_running=self._running,
            managed_vram_mode=self.managed_vram_mode,
        )

    async def stop(self) -> ProcessActionResult:
        self.stop_count += 1
        self._running = False
        return ProcessActionResult(status="stopped", comfyui=await self.status())

    async def start(self) -> ProcessActionResult:
        self.start_count += 1
        self._running = True
        return ProcessActionResult(status="started", comfyui=await self.status())


def _service(
    tmp_path: Path,
    manager: FakeRuntimeManager,
) -> tuple[ComfyUISidecarService, ComfyUILaunchSettingsStore]:
    store = ComfyUILaunchSettingsStore(
        tmp_path / "runtime-store" / "settings" / "comfyui-launch.json"
    )
    return (
        ComfyUISidecarService(
            runtime_manager=manager,  # type: ignore[arg-type]
            launch_settings_store=store,
        ),
        store,
    )


def test_comfyui_launch_settings_default_to_normal_vram(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, FakeRuntimeManager(mode="managed"))

    settings = service.comfyui_launch_settings()

    assert settings.vram_mode == "normal"
    assert settings.applies_to_managed_runtime is True


@pytest.mark.anyio
async def test_comfyui_launch_setting_persists_without_restart_when_not_running(
    tmp_path: Path,
) -> None:
    manager = FakeRuntimeManager(mode="managed", running=False)
    service, store = _service(tmp_path, manager)

    result = await service.update_comfyui_launch_settings(
        ComfyUILaunchSettings(vram_mode="lowvram")
    )

    assert result.status == "updated"
    assert result.restart_status is None
    assert manager.managed_vram_mode == "lowvram"
    assert manager.stop_count == 0
    assert manager.start_count == 0
    assert store.read().vram_mode == "lowvram"


@pytest.mark.anyio
async def test_comfyui_launch_setting_restarts_running_managed_runtime(tmp_path: Path) -> None:
    manager = FakeRuntimeManager(mode="managed", running=True)
    service, store = _service(tmp_path, manager)

    result = await service.update_comfyui_launch_settings(
        ComfyUILaunchSettings(vram_mode="cpu")
    )

    assert result.status == "updated_restarted"
    assert result.restart_status == "started"
    assert manager.managed_vram_mode == "cpu"
    assert manager.stop_count == 1
    assert manager.start_count == 1
    assert store.read().vram_mode == "cpu"


@pytest.mark.anyio
async def test_comfyui_launch_setting_is_blocked_in_external_mode(tmp_path: Path) -> None:
    manager = FakeRuntimeManager(mode="external", running=False)
    service, store = _service(tmp_path, manager)

    result = await service.update_comfyui_launch_settings(
        ComfyUILaunchSettings(vram_mode="lowvram")
    )

    assert result.status == "blocked"
    assert result.settings.applies_to_managed_runtime is False
    assert manager.managed_vram_mode == "normal"
    assert store.read().vram_mode == "normal"
