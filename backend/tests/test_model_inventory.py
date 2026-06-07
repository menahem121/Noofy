from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import Response
from fastapi.testclient import TestClient

from app.api.routes.models import list_models
from app.diagnostics import LogStore
from app.engine.models import ModelInfo
from app.main import create_app
from app.models.inventory import (
    ModelDownloadStartRequest,
    ModelInventoryService,
    ModelOwnershipStore,
    ModelTagStore,
)
from app.models.downloads import ModelDownloadJobService
from app.models.folders import ModelFolderSettingsService, ModelFolderSettingsStore, ModelFolderUpdateRequest
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import RequiredModel, WorkflowImportMetadata, WorkflowMetadata, WorkflowPackage


def _package(
    models: list[RequiredModel],
    *,
    workflow_id: str = "wf_text",
    name: str = "Text workflow",
    import_metadata: WorkflowImportMetadata | None = None,
) -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(id=workflow_id, name=name, version="0.1.0"),
        engine="comfyui",
        required_models=models,
        comfyui_graph={},
        import_metadata=import_metadata,
    )


class FakeWorkflowLoader:
    def __init__(self, packages: list[WorkflowPackage], sources: dict[str, str] | None = None) -> None:
        self.packages = packages
        self.sources = sources or {}

    def list_packages(self) -> list[WorkflowPackage]:
        return self.packages

    def list_packages_with_sources(self) -> list[tuple[WorkflowPackage, str]]:
        return [(package, self.sources.get(package.metadata.id, "imported")) for package in self.packages]

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        for package in self.packages:
            if package.metadata.id == workflow_id:
                return package
        raise KeyError(workflow_id)


class FakeEngineService:
    def __init__(
        self,
        *,
        noofy_root: Path,
        external_root: Path | None,
        packages: list[WorkflowPackage],
        workflow_sources: dict[str, str] | None = None,
    ) -> None:
        self.log_store = LogStore()
        roots = [noofy_root]
        if external_root is not None:
            roots.append(external_root)
        self.model_availability_service = ModelAvailabilityService(
            model_roots=roots,
            noofy_models_dir=noofy_root,
            log_store=self.log_store,
        )
        self.workflow_loader = FakeWorkflowLoader(packages, workflow_sources)

    async def list_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(folder="configs", filename="engine-config.yaml"),
            ModelInfo(folder="vae", filename="engine-only.safetensors"),
        ]

    async def shutdown(self) -> None:
        return None


class SlowEngineVisibleModelsService(FakeEngineService):
    async def list_available_models(self) -> list[ModelInfo]:
        await asyncio.sleep(5)
        return [ModelInfo(folder="vae", filename="slow-engine-only.safetensors")]


class MaterializedEngineVisibleModelsService(FakeEngineService):
    def __init__(self, *, materialized_model: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.materialized_model = materialized_model

    async def list_available_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                folder="upscale_models",
                filename=self.materialized_model.name,
                path=str(self.materialized_model),
            ),
            ModelInfo(folder="vae", filename="engine-only.safetensors"),
        ]


class PathlessEngineVisibleModelsService(FakeEngineService):
    async def list_available_models(self) -> list[ModelInfo]:
        return [ModelInfo(folder="upscale_models", filename="runtime-only.safetensors")]


def _client(
    tmp_path: Path,
    packages: list[WorkflowPackage],
    *,
    workflow_sources: dict[str, str] | None = None,
) -> TestClient:
    noofy_root = tmp_path / "Noofy Models"
    external_root = tmp_path / "ComfyUI" / "models"
    external_root.mkdir(parents=True, exist_ok=True)
    model_folder_service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    model_folder_service.update(
        ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root), external_comfyui_models_dir=str(external_root))
    )
    engine = FakeEngineService(
        noofy_root=noofy_root,
        external_root=external_root,
        packages=packages,
        workflow_sources=workflow_sources,
    )
    return TestClient(
        create_app(
            engine_service=engine,
            model_folder_service=model_folder_service,
            model_tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
            model_ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
        )
    )


def test_models_inventory_combines_local_external_engine_and_missing_models(tmp_path: Path) -> None:
    noofy_model = tmp_path / "Noofy Models" / "checkpoints" / "base.safetensors"
    noofy_model.parent.mkdir(parents=True)
    noofy_model.write_bytes(b"base")
    external_model = tmp_path / "ComfyUI" / "models" / "loras" / "style.safetensors"
    external_model.parent.mkdir(parents=True)
    external_model.write_bytes(b"style")
    diffusion_model = tmp_path / "ComfyUI" / "models" / "diffusion_models" / "flux.safetensors"
    diffusion_model.parent.mkdir(parents=True)
    diffusion_model.write_bytes(b"flux")
    noofy_config = tmp_path / "Noofy Models" / "configs" / "v1-inference.yaml"
    noofy_config.parent.mkdir(parents=True)
    noofy_config.write_text("model:\n  target: ignored\n", encoding="utf-8")
    external_config = tmp_path / "ComfyUI" / "models" / "configs" / "anything_v3.yaml"
    external_config.parent.mkdir(parents=True)
    external_config.write_text("model:\n  target: ignored\n", encoding="utf-8")
    package = _package(
        [
            RequiredModel(folder="checkpoints", filename="base.safetensors", size_bytes=4, verification_level="filename_size"),
            RequiredModel(folder="controlnet", filename="missing.safetensors", size_bytes=12, source_url="https://example.test/missing.safetensors"),
        ]
    )

    with _client(tmp_path, [package]) as client:
        tag_response = client.post("/api/models/tags", json={"name": "Starter", "color": "#4ade80"})
        tag_id = tag_response.json()["id"]
        client.put("/api/models/checkpoints/base.safetensors/tags", json={"tag_ids": [tag_id]})
        response = client.get("/api/models")

    assert response.status_code == 200
    data = response.json()
    by_key = {model["model_key"]: model for model in data["models"]}
    assert data["summary"]["total_count"] == 5
    assert data["summary"]["noofy_count"] == 1
    assert data["summary"]["external_comfyui_count"] == 2
    assert data["summary"]["missing_count"] == 1
    assert isinstance(data["summary"]["disk_free_bytes"], int)
    assert data["summary"]["disk_free_bytes"] > 0
    assert by_key["checkpoints/base.safetensors"]["source_label"] == "Noofy Models"
    assert by_key["checkpoints/base.safetensors"]["ownership"] == "noofy_local"
    assert by_key["checkpoints/base.safetensors"]["can_delete"] is False
    assert by_key["checkpoints/base.safetensors"]["delete_unavailable_reason"] == "Only models imported or downloaded by Noofy can be deleted."
    assert by_key["checkpoints/base.safetensors"]["tag_ids"] == [tag_id]
    assert by_key["loras/style.safetensors"]["source_label"] == "ComfyUI models folder"
    assert by_key["loras/style.safetensors"]["can_delete"] is True
    assert by_key["loras/style.safetensors"]["delete_unavailable_reason"] is None
    assert by_key["diffusion_models/flux.safetensors"]["model_type"] == "checkpoint"
    assert by_key["vae/engine-only.safetensors"]["source_label"] == "Visible to engine"
    assert by_key["controlnet/missing.safetensors"]["source_label"] == "Required by workflow"
    assert by_key["controlnet/missing.safetensors"]["downloadable_references"][0]["workflow_id"] == "wf_text"
    assert "configs/v1-inference.yaml" not in by_key
    assert "configs/anything_v3.yaml" not in by_key
    assert "configs/engine-config.yaml" not in by_key


def test_models_inventory_disk_free_is_live_and_not_http_cacheable(tmp_path: Path, monkeypatch) -> None:
    disk_free_values = iter([111, 222])

    def fake_disk_usage(_path: Path) -> SimpleNamespace:
        return SimpleNamespace(free=next(disk_free_values))

    monkeypatch.setattr("app.models.inventory.shutil.disk_usage", fake_disk_usage)
    noofy_root = tmp_path / "Noofy Models"
    settings_service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
    engine = FakeEngineService(noofy_root=noofy_root, external_root=None, packages=[])
    service = ModelInventoryService(
        engine_service=engine,
        model_folder_service=settings_service,
        tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
        ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
        log_store=engine.log_store,
    )

    async def run() -> tuple[Response, int | None, int | None]:
        first_response = Response()
        first_inventory = await list_models(inventory=service, response=first_response)
        second_response = Response()
        second_inventory = await list_models(inventory=service, response=second_response)
        return first_response, first_inventory.summary.disk_free_bytes, second_inventory.summary.disk_free_bytes

    response, first_disk_free, second_disk_free = asyncio.run(run())

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert first_disk_free == 111
    assert second_disk_free == 222


def test_models_inventory_does_not_wait_on_slow_engine_visible_models(tmp_path: Path) -> None:
    async def run() -> None:
        noofy_root = tmp_path / "Noofy Models"
        noofy_model = noofy_root / "checkpoints" / "base.safetensors"
        noofy_model.parent.mkdir(parents=True)
        noofy_model.write_bytes(b"base")
        settings_service = ModelFolderSettingsService(
            store=ModelFolderSettingsStore(
                tmp_path / "settings" / "model-folders.json"
            ),
            default_noofy_models_dir=noofy_root,
        )
        settings_service.update(
            ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root))
        )
        engine = SlowEngineVisibleModelsService(
            noofy_root=noofy_root,
            external_root=None,
            packages=[],
        )
        service = ModelInventoryService(
            engine_service=engine,
            model_folder_service=settings_service,
            tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
            ownership_store=ModelOwnershipStore(
                tmp_path / "settings" / "model-ownership.json"
            ),
            log_store=engine.log_store,
            engine_visible_models_timeout_seconds=0.01,
        )

        inventory = await asyncio.wait_for(service.inventory(), timeout=1)

        keys = {model.model_key for model in inventory.models}
        assert "checkpoints/base.safetensors" in keys
        assert "vae/slow-engine-only.safetensors" not in keys
        assert any(
            event.message == "Skipped slow engine-visible model enrichment"
            for event in engine.log_store.list_events().events
        )

    asyncio.run(run())


def test_models_inventory_excludes_runtime_materialized_engine_models(tmp_path: Path) -> None:
    async def run() -> None:
        noofy_root = tmp_path / "Noofy Models"
        materialized_root = tmp_path / "model-store" / "materialized"
        materialized_model = (
            materialized_root
            / "views"
            / "model-view-test"
            / "upscale_models"
            / "runtime-only.safetensors"
        )
        materialized_model.parent.mkdir(parents=True)
        materialized_model.write_bytes(b"runtime-only")
        settings_service = ModelFolderSettingsService(
            store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
            default_noofy_models_dir=noofy_root,
        )
        settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
        engine = MaterializedEngineVisibleModelsService(
            noofy_root=noofy_root,
            external_root=None,
            packages=[],
            materialized_model=materialized_model,
        )
        service = ModelInventoryService(
            engine_service=engine,
            model_folder_service=settings_service,
            tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
            ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
            log_store=engine.log_store,
            excluded_engine_model_roots=[materialized_root],
        )

        inventory = await service.inventory()

        keys = {model.model_key for model in inventory.models}
        assert "upscale_models/runtime-only.safetensors" not in keys
        assert "vae/engine-only.safetensors" in keys

    asyncio.run(run())


def test_workflow_availability_overrides_pathless_engine_visible_model(tmp_path: Path) -> None:
    async def run() -> None:
        noofy_root = tmp_path / "Noofy Models"
        settings_service = ModelFolderSettingsService(
            store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
            default_noofy_models_dir=noofy_root,
        )
        settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
        engine = PathlessEngineVisibleModelsService(
            noofy_root=noofy_root,
            external_root=None,
            packages=[
                _package(
                    [
                        RequiredModel(
                            folder="upscale_models",
                            filename="runtime-only.safetensors",
                            size_bytes=12,
                            source_url="https://example.test/runtime-only.safetensors",
                        )
                    ]
                )
            ],
        )
        service = ModelInventoryService(
            engine_service=engine,
            model_folder_service=settings_service,
            tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
            ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
            log_store=engine.log_store,
        )

        inventory = await service.inventory()

        model = next(item for item in inventory.models if item.model_key == "upscale_models/runtime-only.safetensors")
        assert model.status == "missing"
        assert model.source == "required_by_workflow"
        assert model.path is None

    asyncio.run(run())


def test_models_inventory_uses_shallow_workflow_requirement_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_if_hashed(path: Path) -> str:
        raise AssertionError(f"Inventory should not hash model files while listing: {path}")

    monkeypatch.setattr("app.workflows.model_availability._sha256_file", fail_if_hashed)
    model_path = tmp_path / "Noofy Models" / "checkpoints" / "hashed.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"hashed")
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="hashed.safetensors",
                size_bytes=6,
                checksum="sha256:" + "a" * 64,
                verification_level="sha256_size",
                source_url="https://example.test/hashed.safetensors",
            ),
        ]
    )

    with _client(tmp_path, [package]) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    by_key = {model["model_key"]: model for model in response.json()["models"]}
    entry = by_key["checkpoints/hashed.safetensors"]
    assert entry["workflow_usage"][0]["status"] == "possible_match"
    # A local file the user already has, referenced by a workflow but not yet
    # hash-verified, is benign: present it as a neutral "Never used" state instead
    # of the alarming "Needs attention" warning that implies required user action.
    assert entry["status"] == "never_used"
    assert entry["status_label"] == "Never used"
    assert "hasn't been used in a workflow yet" in entry["message"]


def test_models_inventory_omits_unused_bundled_workflow_missing_requirements(tmp_path: Path) -> None:
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="native-only-missing.safetensors",
                size_bytes=10,
                source_url="https://example.test/native-only-missing.safetensors",
            ),
        ],
        workflow_id="native_text",
        name="Native text workflow",
    )

    with _client(tmp_path, [package], workflow_sources={"native_text": "bundled"}) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    data = response.json()
    by_key = {model["model_key"]: model for model in data["models"]}
    assert "checkpoints/native-only-missing.safetensors" not in by_key
    assert data["summary"]["missing_count"] == 0


def test_models_inventory_includes_imported_workflow_missing_requirements(tmp_path: Path) -> None:
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="imported-missing.safetensors",
                size_bytes=10,
                source_url="https://example.test/imported-missing.safetensors",
            ),
        ],
        workflow_id="imported_text",
        name="Imported text workflow",
        import_metadata=WorkflowImportMetadata(original_filename="imported.noofy"),
    )

    with _client(tmp_path, [package], workflow_sources={"imported_text": "imported"}) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    by_key = {model["model_key"]: model for model in response.json()["models"]}
    missing = by_key["checkpoints/imported-missing.safetensors"]
    assert missing["source_label"] == "Required by workflow"
    assert missing["downloadable_references"][0]["workflow_id"] == "imported_text"


def test_model_import_copies_only_into_noofy_models_and_reports_collisions(tmp_path: Path) -> None:
    source = tmp_path / "Downloads" / "demo.safetensors"
    source.parent.mkdir()
    source.write_bytes(b"demo")

    with _client(tmp_path, []) as client:
        imported = client.post(
            "/api/models/import",
            json={"source_paths": [str(source)], "folder": "checkpoints"},
        )
        collision = client.post(
            "/api/models/import",
            json={"source_paths": [str(source)], "folder": "checkpoints"},
        )
        invalid = client.post(
            "/api/models/import",
            json={"source_paths": [str(source)], "folder": "../bad"},
        )

    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 1
    target = tmp_path / "Noofy Models" / "checkpoints" / "demo.safetensors"
    assert target.read_bytes() == b"demo"
    with _client(tmp_path, []) as client:
        inventory = client.get("/api/models").json()
    imported_model = {model["model_key"]: model for model in inventory["models"]}["checkpoints/demo.safetensors"]
    assert imported_model["ownership"] == "noofy_imported"
    assert imported_model["can_delete"] is True
    assert collision.status_code == 200
    assert collision.json()["failed_count"] == 1
    assert "already exists" in collision.json()["models"][0]["message"]
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["message"] == "Unsupported model folder: ../bad"


def test_model_inventory_cleanable_size_counts_only_unused_noofy_owned_models(tmp_path: Path) -> None:
    # Downloaded by Noofy, not required by any workflow -> cleanable.
    unused_owned = tmp_path / "Noofy Models" / "loras" / "unused.safetensors"
    unused_owned.parent.mkdir(parents=True)
    unused_owned.write_bytes(b"unused-owned")
    # Imported by Noofy but required by a workflow -> not cleanable.
    used_owned = tmp_path / "Noofy Models" / "checkpoints" / "base.safetensors"
    used_owned.parent.mkdir(parents=True)
    used_owned.write_bytes(b"used-owned")
    # User-owned local Noofy file (not downloaded/imported) -> not cleanable.
    local_only = tmp_path / "Noofy Models" / "loras" / "local.safetensors"
    local_only.write_bytes(b"local-only")
    # External ComfyUI file -> never cleanable even though deletable.
    external = tmp_path / "ComfyUI" / "models" / "loras" / "external.safetensors"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"external")

    ownership_store = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")
    ownership_store.mark_downloaded("loras/unused.safetensors")
    ownership_store.mark_imported("checkpoints/base.safetensors")

    package = _package(
        [RequiredModel(folder="checkpoints", filename="base.safetensors", size_bytes=10, verification_level="filename_size")]
    )

    with _client(tmp_path, [package]) as client:
        data = client.get("/api/models").json()

    assert data["summary"]["cleanable_size_bytes"] == unused_owned.stat().st_size
    by_key = {model["model_key"]: model for model in data["models"]}
    assert by_key["loras/unused.safetensors"]["workflow_usage"] == []
    assert by_key["checkpoints/base.safetensors"]["workflow_usage"] != []


def test_model_inventory_ignores_partial_import_transactions(tmp_path: Path) -> None:
    partial = tmp_path / "Noofy Models" / ".imports" / "tx" / "partial.safetensors"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    visible = tmp_path / "Noofy Models" / "checkpoints" / "ready.safetensors"
    visible.parent.mkdir(parents=True)
    visible.write_bytes(b"ready")

    with _client(tmp_path, []) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    keys = {model["model_key"] for model in response.json()["models"]}
    assert "checkpoints/ready.safetensors" in keys
    assert "checkpoints/partial.safetensors" not in keys


def test_model_delete_removes_noofy_owned_and_external_comfyui_model_files(tmp_path: Path) -> None:
    noofy_model = tmp_path / "Noofy Models" / "checkpoints" / "base.safetensors"
    noofy_model.parent.mkdir(parents=True)
    noofy_model.write_bytes(b"base")
    owned_model = tmp_path / "Noofy Models" / "checkpoints" / "owned.safetensors"
    owned_model.write_bytes(b"owned")
    external_model = tmp_path / "ComfyUI" / "models" / "loras" / "style.safetensors"
    external_model.parent.mkdir(parents=True)
    external_model.write_bytes(b"style")
    ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json").mark_imported("checkpoints/owned.safetensors")

    with _client(tmp_path, []) as client:
        blocked_local = client.delete("/api/models/checkpoints/base.safetensors")
        deleted = client.delete("/api/models/checkpoints/owned.safetensors")
        deleted_external = client.delete("/api/models/loras/style.safetensors")

    assert blocked_local.status_code == 400
    assert blocked_local.json()["detail"]["message"] == "Noofy can delete only models it imported or downloaded."
    assert noofy_model.exists()
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert not owned_model.exists()
    assert deleted_external.status_code == 200
    assert deleted_external.json()["message"] == "Model file deleted from ComfyUI models folder."
    assert not external_model.exists()


class FakeDownloadAvailabilityService:
    def __init__(self, noofy_root: Path, *, fail: bool = False) -> None:
        self.noofy_root = noofy_root
        self.fail = fail

    async def download_missing(self, package, *, progress_callback=None, cancel_event=None):
        if self.fail:
            raise RuntimeError("provider failed token=secret-token")
        for index, model in enumerate(package.required_models, start=1):
            target = self.noofy_root / model.folder / model.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"model")
            if progress_callback is not None:
                progress_callback(
                    {
                        "requirement_id": f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}",
                        "filename": model.filename,
                        "status": "completed",
                        "bytes_downloaded": 5,
                        "total_bytes": 5,
                        "model_index": index,
                    }
                )
        return SimpleNamespace(failed_count=0, status="completed")


class SparseFailureDownloadAvailabilityService:
    async def download_missing(self, package, *, progress_callback=None, cancel_event=None):
        model = package.required_models[0]
        if progress_callback is not None:
            progress_callback(
                {
                    "requirement_id": f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}",
                    "filename": model.filename,
                    "status": "rate_limited",
                    "message": "Rate limited.",
                    "model_index": 1,
                }
            )
        return SimpleNamespace(
            failed_count=1,
            downloaded_count=0,
            status="completed_with_errors",
        )


def test_model_download_job_tracks_active_job_and_downloaded_ownership(tmp_path: Path) -> None:
    async def run() -> None:
        await _assert_download_job_tracks_active_job_and_downloaded_ownership(tmp_path)

    asyncio.run(run())


async def _assert_download_job_tracks_active_job_and_downloaded_ownership(tmp_path: Path) -> None:
    noofy_root = tmp_path / "Noofy Models"
    settings_service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
    model = RequiredModel(
        folder="checkpoints",
        filename="downloaded.safetensors",
        size_bytes=5,
        node_id="1",
        input_name="model",
        source_url="https://example.test/downloaded.safetensors",
    )
    engine = FakeEngineService(noofy_root=noofy_root, external_root=None, packages=[_package([model])])
    engine.model_availability_service = FakeDownloadAvailabilityService(noofy_root)
    ownership_store = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")
    service = ModelDownloadJobService(
        engine_service=engine,
        model_folder_service=settings_service,
        ownership_store=ownership_store,
        log_store=engine.log_store,
    )

    started = service.start(
        ModelDownloadStartRequest(
            selections=[{"workflow_id": "wf_text", "requirement_id": "1:model:checkpoints/downloaded.safetensors"}]
        )
    )

    assert service.active().job is not None
    job = service._jobs[started.job_id]
    assert job.task is not None
    await job.task

    status = service.status(started.job_id)
    assert status.status == "completed"
    assert status.percent == 100
    assert ownership_store.origin_for_model("checkpoints/downloaded.safetensors") == "downloaded"


def test_model_download_job_preserves_totals_when_failure_progress_is_sparse(tmp_path: Path) -> None:
    async def run() -> None:
        await _assert_model_download_job_preserves_totals_when_failure_progress_is_sparse(tmp_path)

    asyncio.run(run())


async def _assert_model_download_job_preserves_totals_when_failure_progress_is_sparse(tmp_path: Path) -> None:
    noofy_root = tmp_path / "Noofy Models"
    settings_service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
    model = RequiredModel(
        folder="checkpoints",
        filename="rate-limited.safetensors",
        size_bytes=10,
        node_id="1",
        input_name="model",
        source_url="https://example.test/rate-limited.safetensors",
    )
    engine = FakeEngineService(noofy_root=noofy_root, external_root=None, packages=[_package([model])])
    engine.model_availability_service = SparseFailureDownloadAvailabilityService()
    service = ModelDownloadJobService(
        engine_service=engine,
        model_folder_service=settings_service,
        ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
        log_store=engine.log_store,
    )

    started = service.start(
        ModelDownloadStartRequest(
            selections=[{"workflow_id": "wf_text", "requirement_id": "1:model:checkpoints/rate-limited.safetensors"}]
        )
    )
    job = service._jobs[started.job_id]
    assert job.task is not None
    await job.task

    status = service.status(started.job_id)
    assert status.status == "failed"
    assert status.total_bytes == 10
    assert status.models[0].total_bytes == 10
    assert status.models[0].status == "rate_limited"


def test_model_download_job_sanitizes_provider_failure_diagnostics(tmp_path: Path) -> None:
    async def run() -> None:
        await _assert_download_job_sanitizes_provider_failure_diagnostics(tmp_path)

    asyncio.run(run())


async def _assert_download_job_sanitizes_provider_failure_diagnostics(tmp_path: Path) -> None:
    noofy_root = tmp_path / "Noofy Models"
    settings_service = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    settings_service.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
    model = RequiredModel(
        folder="checkpoints",
        filename="missing.safetensors",
        node_id="1",
        input_name="model",
        source_url="https://example.test/missing.safetensors",
    )
    engine = FakeEngineService(noofy_root=noofy_root, external_root=None, packages=[_package([model])])
    engine.model_availability_service = FakeDownloadAvailabilityService(noofy_root, fail=True)
    service = ModelDownloadJobService(
        engine_service=engine,
        model_folder_service=settings_service,
        ownership_store=ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json"),
        log_store=engine.log_store,
    )

    started = service.start(
        ModelDownloadStartRequest(
            selections=[{"workflow_id": "wf_text", "requirement_id": "1:model:checkpoints/missing.safetensors"}]
        )
    )
    job = service._jobs[started.job_id]
    assert job.task is not None
    await job.task

    events = engine.log_store.list_events().events
    assert service.status(started.job_id).status == "failed"
    assert "secret-token" not in str(events[-1].details)
    assert "<redacted>" in str(events[-1].details)
