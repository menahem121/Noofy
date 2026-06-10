from pathlib import Path

import pytest

from app.engine.models import ComfyUIRuntimeStatus, ProcessActionResult
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.comfyui.launch_settings import (
    LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    LAUNCH_DEFAULT_EMITTED_AS_ENV,
    LAUNCH_DEFAULT_FINGERPRINT_ONLY,
    RUNTIME_LAUNCH_DEFAULTS_FIELD_BEHAVIOR,
    ComfyUILaunchSettings,
    ComfyUILaunchSettingsStore,
    comfyui_attention_args,
    comfyui_precision_args,
    comfyui_preview_args,
    comfyui_vram_args,
)
from app.runtime.profiles.profiles import RuntimeLaunchDefaults


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


def test_comfyui_preview_args_default_to_auto_512() -> None:
    assert comfyui_preview_args() == [
        "--preview-method",
        "auto",
        "--preview-size",
        "512",
    ]

    with pytest.raises(ValueError, match="Unsupported ComfyUI preview method"):
        comfyui_preview_args("invalid", 512)

    with pytest.raises(ValueError, match="Unsupported ComfyUI preview size"):
        comfyui_preview_args("auto", 0)


def test_comfyui_attention_args_map_profile_backends_to_flags() -> None:
    assert comfyui_attention_args() == []
    assert comfyui_attention_args("auto") == []
    assert comfyui_attention_args("pytorch_sdpa") == ["--use-pytorch-cross-attention"]

    with pytest.raises(ValueError, match="Unsupported ComfyUI attention backend"):
        comfyui_attention_args("flash_attn_direct")


def test_comfyui_precision_args_allow_only_quality_neutral_auto() -> None:
    assert comfyui_precision_args() == []
    assert comfyui_precision_args("auto") == []

    with pytest.raises(ValueError, match="Unsupported ComfyUI precision policy"):
        comfyui_precision_args("force_fp16")


def test_comfyui_vram_args_accept_profile_auto_as_no_flag() -> None:
    assert comfyui_vram_args("auto") == []
    assert comfyui_vram_args("cpu") == ["--cpu"]

    with pytest.raises(ValueError, match="Unsupported ComfyUI VRAM mode"):
        comfyui_vram_args("turbo")


def test_every_runtime_launch_default_field_is_emitted_or_fingerprint_only() -> None:
    """No RuntimeLaunchDefaults field may silently become fingerprint-only."""
    registered = set(RUNTIME_LAUNCH_DEFAULTS_FIELD_BEHAVIOR)
    model_fields = set(RuntimeLaunchDefaults.model_fields)

    assert registered == model_fields, (
        "RuntimeLaunchDefaults fields and RUNTIME_LAUNCH_DEFAULTS_FIELD_BEHAVIOR "
        "are out of sync. Register new fields as emitted launch args/env, or "
        f"explicitly mark them fingerprint-only. Unregistered: {model_fields - registered}; "
        f"stale: {registered - model_fields}."
    )
    allowed = {
        LAUNCH_DEFAULT_EMITTED_AS_ARGS,
        LAUNCH_DEFAULT_EMITTED_AS_ENV,
        LAUNCH_DEFAULT_FINGERPRINT_ONLY,
    }
    assert set(RUNTIME_LAUNCH_DEFAULTS_FIELD_BEHAVIOR.values()) <= allowed


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
