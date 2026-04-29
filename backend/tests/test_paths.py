import sys
from pathlib import Path

import pytest

from app.core.paths import NoofyPaths, resolve_paths


class TestPlatformDefaults:
    def test_macos_default_uses_application_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        env: dict[str, str] = {}

        paths = resolve_paths(env=env)

        assert paths.data_dir == Path.home() / "Library" / "Application Support" / "Noofy"

    def test_windows_default_uses_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        env = {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}

        paths = resolve_paths(env=env)

        assert paths.data_dir == Path("C:\\Users\\test\\AppData\\Roaming") / "Noofy"

    def test_linux_default_uses_local_share(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        env: dict[str, str] = {}

        paths = resolve_paths(env=env)

        assert paths.data_dir == Path.home() / ".local" / "share" / "noofy"

    def test_linux_respects_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        env = {"XDG_DATA_HOME": "/custom/data"}

        paths = resolve_paths(env=env)

        assert paths.data_dir == Path("/custom/data/noofy")


class TestNoofyDataDirOverride:
    def test_noofy_data_dir_overrides_base(self) -> None:
        env = {"NOOFY_DATA_DIR": "/tmp/noofy-test"}

        paths = resolve_paths(env=env)

        assert paths.data_dir == Path("/tmp/noofy-test")
        assert paths.runtime_dir == Path("/tmp/noofy-test/runtime")
        assert paths.models_dir == Path("/tmp/noofy-test/models")
        assert paths.user_workflows_dir == Path("/tmp/noofy-test/workflows")
        assert paths.outputs_dir == Path("/tmp/noofy-test/outputs")
        assert paths.logs_dir == Path("/tmp/noofy-test/logs")
        assert paths.cache_dir == Path("/tmp/noofy-test/cache")
        assert paths.temp_dir == Path("/tmp/noofy-test/temp")


class TestTargetedOverrides:
    def test_specific_env_vars_override_individual_dirs(self) -> None:
        env = {
            "NOOFY_DATA_DIR": "/tmp/base",
            "NOOFY_MODELS_DIR": "/custom/models",
            "NOOFY_LOGS_DIR": "/custom/logs",
        }

        paths = resolve_paths(env=env)

        # Overridden
        assert paths.models_dir == Path("/custom/models")
        assert paths.logs_dir == Path("/custom/logs")
        # Not overridden – falls back to base
        assert paths.runtime_dir == Path("/tmp/base/runtime")
        assert paths.cache_dir == Path("/tmp/base/cache")

    def test_noofy_runtime_dir_backward_compat(self) -> None:
        env = {
            "NOOFY_DATA_DIR": "/tmp/base",
            "NOOFY_RUNTIME_DIR": "/legacy/runtime",
        }

        paths = resolve_paths(env=env)

        assert paths.runtime_dir == Path("/legacy/runtime")
        # Other dirs still under data_dir
        assert paths.models_dir == Path("/tmp/base/models")

    def test_comfyui_repo_dir_override(self) -> None:
        env = {"COMFYUI_REPO_DIR": "/opt/comfyui"}

        paths = resolve_paths(env=env)

        assert paths.comfyui_repo_dir == Path("/opt/comfyui")


class TestBundledWorkflowsDir:
    def test_bundled_workflows_always_in_source_tree(self) -> None:
        env = {"NOOFY_DATA_DIR": "/tmp/noofy"}

        paths = resolve_paths(env=env)

        # Should point inside the backend source tree, not under data_dir
        assert "workflows" in str(paths.bundled_workflows_dir)
        assert "packages" in str(paths.bundled_workflows_dir)
        assert str(paths.bundled_workflows_dir) != str(paths.user_workflows_dir)


class TestEnsureDirectories:
    def test_ensure_directories_creates_writable_dirs(self, tmp_path: Path) -> None:
        paths = NoofyPaths(
            data_dir=tmp_path / "data",
            runtime_dir=tmp_path / "data" / "runtime",
            models_dir=tmp_path / "data" / "models",
            user_workflows_dir=tmp_path / "data" / "workflows",
            outputs_dir=tmp_path / "data" / "outputs",
            logs_dir=tmp_path / "data" / "logs",
            cache_dir=tmp_path / "data" / "cache",
            temp_dir=tmp_path / "data" / "temp",
            bundled_workflows_dir=tmp_path / "bundled",
            comfyui_repo_dir=tmp_path / "repo",
        )

        paths.ensure_directories()

        assert paths.data_dir.is_dir()
        assert paths.runtime_dir.is_dir()
        assert paths.models_dir.is_dir()
        assert paths.user_workflows_dir.is_dir()
        assert paths.outputs_dir.is_dir()
        assert paths.logs_dir.is_dir()
        assert paths.cache_dir.is_dir()
        assert paths.temp_dir.is_dir()
        # bundled/repo are NOT created
        assert not paths.bundled_workflows_dir.exists()
        assert not paths.comfyui_repo_dir.exists()


class TestWritableStatus:
    def test_writable_status_reports_all_dirs(self, tmp_path: Path) -> None:
        paths = NoofyPaths(
            data_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
            models_dir=tmp_path / "models",
            user_workflows_dir=tmp_path / "workflows",
            outputs_dir=tmp_path / "outputs",
            logs_dir=tmp_path / "logs",
            cache_dir=tmp_path / "cache",
            temp_dir=tmp_path / "temp",
            bundled_workflows_dir=tmp_path / "bundled",
            comfyui_repo_dir=tmp_path / "repo",
        )

        status = paths.writable_status()

        assert "data_dir" in status
        assert "models_dir" in status
        assert "comfyui_repo_dir" in status
        # data_dir exists (it's tmp_path)
        assert status["data_dir"]["exists"] is True
        assert status["data_dir"]["writable"] is True
        # models_dir doesn't exist yet
        assert status["models_dir"]["exists"] is False
