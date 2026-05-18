from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.folders import (
    COMFYUI_MODEL_CATEGORIES,
    ModelFolderSettings,
    ModelFolderSettingsService,
    ModelFolderSettingsStore,
    ModelFolderUpdateRequest,
    default_noofy_models_dir,
    ensure_model_subfolders,
    write_extra_model_paths_config,
)


class FakeEngineService:
    def __init__(self) -> None:
        self.applied: tuple[Path, Path | None, Path | None] | None = None

    def apply_model_folder_settings(
        self,
        noofy_models_dir: Path,
        external_comfyui_models_dir: Path | None = None,
        *,
        extra_model_paths_config: Path | None = None,
    ) -> None:
        self.applied = (
            noofy_models_dir,
            external_comfyui_models_dir,
            extra_model_paths_config,
        )

    async def shutdown(self) -> None:
        return None


def test_ensure_model_subfolders_creates_comfyui_categories(tmp_path: Path) -> None:
    root = tmp_path / "Noofy Models"

    ensure_model_subfolders(root)

    assert root.is_dir()
    assert all((root / category).is_dir() for category in COMFYUI_MODEL_CATEGORIES)


def test_model_folder_service_updates_noofy_and_external_paths(tmp_path: Path) -> None:
    default_dir = tmp_path / "Documents" / "Noofy Models"
    external_dir = tmp_path / "ComfyUI" / "models"
    external_dir.mkdir(parents=True)
    applied: list[tuple[Path, Path | None]] = []
    service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=default_dir,
        on_change=lambda noofy, external: applied.append((noofy, external)),
    )

    result = service.update(
        ModelFolderUpdateRequest(
            noofy_models_dir=str(tmp_path / "Custom Noofy Models"),
            external_comfyui_models_dir=str(external_dir),
        )
    )

    assert result.status == "updated"
    assert Path(result.settings.noofy_models_dir).name == "Custom Noofy Models"
    assert result.settings.external_comfyui_models_dir == str(external_dir)
    assert all(
        (Path(result.settings.noofy_models_dir) / category).is_dir()
        for category in COMFYUI_MODEL_CATEGORIES
    )
    assert applied == [(Path(result.settings.noofy_models_dir), external_dir)]


def test_model_folder_service_repairs_accidental_default_folder_typo(tmp_path: Path) -> None:
    default_dir = tmp_path / "Documents" / "Noofy Models"
    typo_dir = tmp_path / "Documents" / "Noofy Modelss"
    model_file = typo_dir / "checkpoints" / "v1-5-pruned-emaonly-fp16.safetensors"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"model")
    store = ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json")
    store.write(ModelFolderSettings(noofy_models_dir=str(typo_dir)))
    service = ModelFolderSettingsService(
        store=store,
        default_noofy_models_dir=default_dir,
    )

    result = service.settings()

    assert result.noofy_models_dir == str(default_dir)
    assert (default_dir / "checkpoints" / "v1-5-pruned-emaonly-fp16.safetensors").read_bytes() == b"model"
    assert not model_file.exists()
    assert store.read(default_noofy_models_dir=default_dir).noofy_models_dir == str(default_dir)


def test_extra_model_paths_config_includes_noofy_and_external_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "runtime-store" / "settings" / "extra-model-paths.yaml"
    noofy_models = tmp_path / "Noofy Models"
    external_models = tmp_path / "ComfyUI" / "models"

    write_extra_model_paths_config(
        config_path,
        noofy_models_dir=noofy_models,
        external_comfyui_models_dir=external_models,
    )

    text = config_path.read_text(encoding="utf-8")
    assert str(noofy_models) in text
    assert str(external_models) in text
    assert "is_default: true" in text
    assert "checkpoints" in text
    assert "loras" in text


def test_model_folder_routes_do_not_modify_external_folder(tmp_path: Path) -> None:
    external_dir = tmp_path / "ComfyUI" / "models"
    external_dir.mkdir(parents=True)
    external_model = external_dir / "checkpoints" / "existing.safetensors"
    external_model.parent.mkdir()
    external_model.write_bytes(b"existing")

    service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=tmp_path / "Documents" / "Noofy Models",
    )

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            model_folder_service=service,
        )
    ) as client:
        response = client.put(
            "/api/settings/model-folders",
            json={
                "noofy_models_dir": str(tmp_path / "Noofy Models"),
                "external_comfyui_models_dir": str(external_dir),
            },
        )

    assert response.status_code == 200
    assert external_model.read_bytes() == b"existing"


def test_paths_endpoint_reports_active_model_folder(tmp_path: Path) -> None:
    service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=tmp_path / "Documents" / "Noofy Models",
    )
    active_dir = tmp_path / "Active Noofy Models"

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            model_folder_service=service,
        )
    ) as client:
        update_response = client.put(
            "/api/settings/model-folders",
            json={"noofy_models_dir": str(active_dir)},
        )
        response = client.get("/api/paths")

    assert update_response.status_code == 200
    assert response.status_code == 200
    assert response.json()["models_dir"]["path"] == str(active_dir)


def test_model_folder_route_rejects_bundled_comfyui_model_paths(tmp_path: Path) -> None:
    service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=tmp_path / "Documents" / "Noofy Models",
    )
    bundled_models = tmp_path / "third_party" / "comfyui" / "models"

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            model_folder_service=service,
        )
    ) as client:
        response = client.put(
            "/api/settings/model-folders",
            json={"noofy_models_dir": str(bundled_models)},
        )

    assert response.status_code == 400


def test_default_noofy_models_dir_uses_documents(tmp_path: Path) -> None:
    assert default_noofy_models_dir(tmp_path).name == "Noofy Models"
