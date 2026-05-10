import sys
import json
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
        assert paths.models_dir == Path.home() / "Documents" / "Noofy Models"
        assert paths.user_workflows_dir == Path("/tmp/noofy-test/workflows")
        assert paths.input_dir == Path("/tmp/noofy-test/input")
        assert paths.outputs_dir == Path("/tmp/noofy-test/outputs")
        assert paths.logs_dir == Path("/tmp/noofy-test/logs")
        assert paths.cache_dir == Path("/tmp/noofy-test/cache")
        assert paths.temp_dir == Path("/tmp/noofy-test/temp")
        assert paths.runtime_store_dir == Path("/tmp/noofy-test/runtime-store")
        assert paths.dependency_envs_dir == Path("/tmp/noofy-test/runtime-store/envs")
        assert paths.dependency_locks_dir == Path("/tmp/noofy-test/runtime-store/dependency-locks")
        assert paths.runner_workspaces_dir == Path("/tmp/noofy-test/runtime-store/runner-workspaces")
        assert paths.core_engines_dir == Path("/tmp/noofy-test/runtime-store/core-engines")
        assert paths.core_envs_dir == Path("/tmp/noofy-test/runtime-store/core-envs")
        assert paths.install_transactions_dir == Path("/tmp/noofy-test/runtime-store/transactions")
        assert paths.workflow_store_dir == Path("/tmp/noofy-test/workflow-store")
        assert paths.workflow_packages_store_dir == Path("/tmp/noofy-test/workflow-store/packages")
        assert paths.custom_node_cache_dir == Path("/tmp/noofy-test/custom-node-cache")
        assert paths.wheel_cache_dir == Path("/tmp/noofy-test/wheel-cache")
        assert paths.model_store_dir == Path("/tmp/noofy-test/model-store")
        assert paths.model_blobs_dir == Path("/tmp/noofy-test/model-store/blobs/sha256")
        assert paths.model_refs_dir == Path("/tmp/noofy-test/model-store/refs")
        assert paths.model_materialized_dir == Path("/tmp/noofy-test/model-store/materialized")
        assert paths.comfyui_custom_nodes_dir == Path("/tmp/noofy-test/custom_nodes")
        assert paths.comfyui_user_dir == Path("/tmp/noofy-test/user-state/comfyui")
        assert paths.comfyui_database_file == Path("/tmp/noofy-test/user-state/comfyui/comfyui.db")
        assert paths.python_cache_dir == Path("/tmp/noofy-test/cache/python")


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
        assert paths.models_dir == Path.home() / "Documents" / "Noofy Models"

    def test_model_folder_settings_override_noofy_models_dir(self, tmp_path: Path) -> None:
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        (settings_dir / "model-folders.json").write_text(
            json.dumps({"noofy_models_dir": str(tmp_path / "User Models")}),
            encoding="utf-8",
        )

        paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path)})

        assert paths.models_dir == tmp_path / "User Models"

    def test_rejects_configured_bundled_comfyui_models_dir(self, tmp_path: Path) -> None:
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        (settings_dir / "model-folders.json").write_text(
            json.dumps(
                {
                    "noofy_models_dir": str(
                        tmp_path / "third_party" / "comfyui" / "models"
                    )
                }
            ),
            encoding="utf-8",
        )

        paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path)})

        assert paths.models_dir == Path.home() / "Documents" / "Noofy Models"

    def test_comfyui_repo_dir_override(self) -> None:
        env = {"COMFYUI_REPO_DIR": "/opt/comfyui"}

        paths = resolve_paths(env=env)

        assert paths.comfyui_repo_dir == Path("/opt/comfyui")

    def test_default_comfyui_repo_dir_uses_app_owned_vendor_source(self) -> None:
        paths = resolve_paths(env={})

        assert paths.comfyui_repo_dir.name == "comfyui"
        assert paths.comfyui_repo_dir.parent.name == "third_party"


class TestBundledWorkflowsDir:
    def test_bundled_workflows_always_in_source_tree(self) -> None:
        env = {"NOOFY_DATA_DIR": "/tmp/noofy"}

        paths = resolve_paths(env=env)

        # Should point inside the backend source tree, not under data_dir
        assert "workflows" in str(paths.bundled_workflows_dir)
        assert "packages" in str(paths.bundled_workflows_dir)
        assert str(paths.bundled_workflows_dir) != str(paths.user_workflows_dir)

    def test_packaged_resource_dir_sets_read_only_resource_defaults(self) -> None:
        env = {
            "NOOFY_DATA_DIR": "/tmp/noofy",
            "NOOFY_BUNDLED_RESOURCE_DIR": "/Applications/Noofy.app/Contents/Resources",
        }

        paths = resolve_paths(env=env)

        assert paths.comfyui_repo_dir == Path(
            "/Applications/Noofy.app/Contents/Resources/noofy-runtime/comfyui"
        )
        assert paths.bundled_workflows_dir == Path(
            "/Applications/Noofy.app/Contents/Resources/noofy-runtime/backend/app/workflows/packages"
        )

    def test_packaged_resource_specific_overrides_win_over_resource_root(self) -> None:
        env = {
            "NOOFY_BUNDLED_RESOURCE_DIR": "/app/resources",
            "NOOFY_BUNDLED_COMFYUI_DIR": "/custom/comfyui",
            "NOOFY_BUNDLED_WORKFLOWS_DIR": "/custom/workflows",
        }

        paths = resolve_paths(env=env)

        assert paths.comfyui_repo_dir == Path("/custom/comfyui")
        assert paths.bundled_workflows_dir == Path("/custom/workflows")


class TestEnsureDirectories:
    def test_ensure_directories_creates_writable_dirs(self, tmp_path: Path) -> None:
        paths = NoofyPaths(
            data_dir=tmp_path / "data",
            runtime_dir=tmp_path / "data" / "runtime",
            models_dir=tmp_path / "data" / "models",
            user_workflows_dir=tmp_path / "data" / "workflows",
            input_dir=tmp_path / "data" / "input",
            outputs_dir=tmp_path / "data" / "outputs",
            logs_dir=tmp_path / "data" / "logs",
            cache_dir=tmp_path / "data" / "cache",
            temp_dir=tmp_path / "data" / "temp",
            bundled_workflows_dir=tmp_path / "bundled",
            comfyui_repo_dir=tmp_path / "repo",
        )

        paths.ensure_directories()

        assert paths.data_dir.is_dir()
        assert paths.settings_dir.is_dir()
        assert paths.runtime_dir.is_dir()
        assert paths.models_dir.is_dir()
        assert paths.comfyui_custom_nodes_dir.is_dir()
        assert paths.user_workflows_dir.is_dir()
        assert paths.input_dir.is_dir()
        assert paths.outputs_dir.is_dir()
        assert paths.logs_dir.is_dir()
        assert paths.cache_dir.is_dir()
        assert paths.python_cache_dir.is_dir()
        assert paths.temp_dir.is_dir()
        assert paths.runtime_store_dir.is_dir()
        assert paths.dependency_envs_dir.is_dir()
        assert paths.dependency_locks_dir.is_dir()
        assert paths.runner_workspaces_dir.is_dir()
        assert paths.core_engines_dir.is_dir()
        assert paths.core_envs_dir.is_dir()
        assert paths.install_transactions_dir.is_dir()
        assert paths.workflow_store_dir.is_dir()
        assert paths.workflow_packages_store_dir.is_dir()
        assert paths.custom_node_cache_dir.is_dir()
        assert paths.wheel_cache_dir.is_dir()
        assert paths.model_store_dir.is_dir()
        assert paths.model_blobs_dir.is_dir()
        assert paths.model_refs_dir.is_dir()
        assert paths.model_materialized_dir.is_dir()
        assert paths.comfyui_user_dir.is_dir()
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
            input_dir=tmp_path / "input",
            outputs_dir=tmp_path / "outputs",
            logs_dir=tmp_path / "logs",
            cache_dir=tmp_path / "cache",
            temp_dir=tmp_path / "temp",
            bundled_workflows_dir=tmp_path / "bundled",
            comfyui_repo_dir=tmp_path / "repo",
        )

        status = paths.writable_status()

        assert "data_dir" in status
        assert "settings_dir" in status
        assert "runtime_store_dir" in status
        assert "dependency_envs_dir" in status
        assert "dependency_locks_dir" in status
        assert "runner_workspaces_dir" in status
        assert "core_engines_dir" in status
        assert "core_envs_dir" in status
        assert "install_transactions_dir" in status
        assert "workflow_store_dir" in status
        assert "custom_node_cache_dir" in status
        assert "wheel_cache_dir" in status
        assert "model_store_dir" in status
        assert "models_dir" in status
        assert "comfyui_custom_nodes_dir" in status
        assert "input_dir" in status
        assert "python_cache_dir" in status
        assert "comfyui_repo_dir" in status
        assert "comfyui_user_dir" in status
        assert "comfyui_database_file" in status
        # data_dir exists (it's tmp_path)
        assert status["data_dir"]["exists"] is True
        assert status["data_dir"]["writable"] is True
        # models_dir doesn't exist yet
        assert status["models_dir"]["exists"] is False
