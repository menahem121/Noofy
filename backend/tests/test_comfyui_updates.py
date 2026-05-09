import zipfile
from pathlib import Path

import pytest

from app.core.paths import resolve_paths
from app.engine.diagnostics import LogStore
from app.engine.service import EngineService
from app.runtime.comfyui_updates import (
    ComfyUIRebuildRequest,
    ComfyUIUpdateRequest,
    ComfyUIUpdateService,
    LocalComfyUIVersionRecord,
    UpstreamComfyUIRelease,
    _archive_recovery_candidates,
    _required_route_status_usable,
    _start_failure_is_repairable,
    resolve_active_runtime_selection,
)
from app.runtime.environment import CommandResult
from app.engine.models import ComfyUIRuntimeStatus, ProcessActionResult
from app.runtime.manager import RuntimeManager
from app.runtime.profiles import (
    RuntimeSourceOriginKind,
    RuntimeSourceStatus,
    build_comfyui_source_manifest,
)


def _release(tag: str, *, prerelease: bool = False) -> UpstreamComfyUIRelease:
    return UpstreamComfyUIRelease(
        tag_name=tag,
        prerelease=prerelease,
        draft=False,
        zipball_url=f"https://example.test/{tag}.zip",
        target_commitish=f"commit-{tag}",
    )


def _source_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ComfyUI-main/main.py", "print('comfy')\n")
        archive.writestr("ComfyUI-main/requirements.txt", "aiohttp\n")
        archive.writestr("ComfyUI-main/comfyui_version.py", "__version__ = 'test'\n")


def _manager(tmp_path: Path) -> RuntimeManager:
    repo = tmp_path / f"bundled-{len(list(tmp_path.glob('bundled-*')))}"
    repo.mkdir()
    (repo / "main.py").write_text("", encoding="utf-8")
    return RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo,
        python_executable="python3",
        health_check=lambda _: _async_health(),
        log_store=LogStore(),
    )


async def _async_health() -> tuple[bool, str | None]:
    return False, "not reachable"


async def _fake_command(command: list[str], cwd: Path | None) -> CommandResult:
    if command[1:3] == ["-m", "venv"]:
        venv = Path(command[3])
        python = venv / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/usr/bin/env python\n", encoding="utf-8")
        return CommandResult(returncode=0)
    return CommandResult(returncode=0)


async def _failing_pip_command(command: list[str], cwd: Path | None) -> CommandResult:
    if command[1:3] == ["-m", "venv"]:
        venv = Path(command[3])
        python = venv / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/usr/bin/env python\n", encoding="utf-8")
        return CommandResult(returncode=0)
    if "-m" in command and "pip" in command:
        return CommandResult(returncode=1, stderr="pip failed")
    return CommandResult(returncode=0)


class FakeRepairManager:
    def __init__(self, repo_dir: Path) -> None:
        self.mode = "managed"
        self.repo_dir = repo_dir
        self.python_executable = "python3"
        self.environment = None
        self.start_calls = 0
        self.reconfigured = False
        self.next_start_status = "started"

    async def status(self) -> ComfyUIRuntimeStatus:
        return ComfyUIRuntimeStatus(
            mode="managed",
            reachable=self.start_calls > 0,
            base_url="http://127.0.0.1:9999",
            repo_dir=str(self.repo_dir),
            managed_process_running=self.start_calls > 0,
            pid=123 if self.start_calls > 0 else None,
        )

    async def start(self) -> ProcessActionResult:
        self.start_calls += 1
        return ProcessActionResult(
            status=self.next_start_status, comfyui=await self.status()
        )

    async def stop(self) -> ProcessActionResult:
        return ProcessActionResult(status="not_running", comfyui=await self.status())

    def is_managed_process_running(self) -> bool:
        return False

    def reconfigure_managed_runtime(
        self, *, repo_dir, python_executable, environment, version_metadata
    ) -> None:
        self.repo_dir = repo_dir
        self.python_executable = python_executable
        self.environment = environment
        self.reconfigured = True


def _write_active(paths, record: LocalComfyUIVersionRecord) -> None:
    import json

    (paths.core_engines_dir / "active-comfyui.json").write_text(
        json.dumps(
            {"schema_version": "0.1.0", "active": record.model_dump(mode="json")}
        ),
        encoding="utf-8",
    )


def _write_active_with_previous(
    paths, active: LocalComfyUIVersionRecord, previous: LocalComfyUIVersionRecord
) -> None:
    import json

    (paths.core_engines_dir / "active-comfyui.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "active": active.model_dump(mode="json"),
                "previous_active": previous.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )


def _source_hash_for_zip(
    source_archive: Path, tmp_path: Path, tag: str = "v0.20.1"
) -> str:
    from app.runtime.comfyui_updates import _extract_github_zip

    extracted = tmp_path / f"extracted-{tag}"
    _extract_github_zip(source_archive, extracted)
    manifest = build_comfyui_source_manifest(
        extracted,
        comfyui_core_version=tag,
        source_origin_kind=RuntimeSourceOriginKind.UPSTREAM_SOURCE_ARCHIVE,
        source_reference=f"https://example.test/{tag}.zip",
        source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
    )
    return manifest.source_hash


@pytest.mark.anyio
async def test_versions_lists_upstream_releases_and_local_status(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    fetch_count = 0

    async def releases() -> list[UpstreamComfyUIRelease]:
        nonlocal fetch_count
        fetch_count += 1
        return [
            _release("v0.20.1"),
            _release("v0.21.0-rc1", prerelease=True),
            _release("v0.19.0"),
        ]

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=_manager(tmp_path),
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        release_fetcher=releases,
        log_store=LogStore(),
    )

    local_versions = await service.versions()
    versions = await service.versions(check_upstream=True)

    assert fetch_count == 1
    assert local_versions.upstream_checked is False
    assert local_versions.latest_tag is None
    assert local_versions.options == []
    assert versions.updates_allowed
    assert versions.upstream_checked
    assert versions.latest_tag == "v0.20.1"
    assert [option.tag for option in versions.options] == ["v0.20.1", "v0.19.0"]
    assert all(option.status == "Available upstream" for option in versions.options)


async def _async_releases(
    releases: list[UpstreamComfyUIRelease],
) -> list[UpstreamComfyUIRelease]:
    return releases


@pytest.mark.anyio
async def test_successful_update_installs_fresh_env_and_activates(
    tmp_path: Path,
) -> None:
    source_archive = tmp_path / "source.zip"
    _source_zip(source_archive)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    manager = _manager(tmp_path)
    smoke_calls: list[tuple[Path, Path, str]] = []

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(source_archive.read_bytes())
        return dest.stat().st_size

    async def smoke(source: Path, env: Path, record):
        smoke_calls.append((source, env, record.tag))

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        release_fetcher=lambda: _async_releases([_release("v0.20.1")]),
        archive_downloader=downloader,
        command_runner=_fake_command,
        smoke_tester=smoke,
        log_store=LogStore(),
    )

    await service._run_update("latest", "job-1")
    status = service.update_status()
    versions = await service.versions()

    assert status.status == "completed"
    assert status.activated_version == "v0.20.1"
    assert smoke_calls and smoke_calls[0][0].is_relative_to(paths.core_engines_dir)
    assert smoke_calls[0][1].is_relative_to(paths.core_envs_dir)
    assert versions.current is not None
    assert versions.current.tag == "v0.20.1"
    assert versions.current.locally_verified
    assert manager.repo_dir == Path(versions.current.source_path)
    assert manager.environment is not None
    assert manager.environment.venv_dir == Path(versions.current.env_path)


@pytest.mark.anyio
async def test_failed_smoke_does_not_change_active_runtime(tmp_path: Path) -> None:
    source_archive = tmp_path / "source.zip"
    _source_zip(source_archive)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    manager = _manager(tmp_path)
    original_repo = manager.repo_dir

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(source_archive.read_bytes())
        return dest.stat().st_size

    async def smoke(source: Path, env: Path, record):
        raise RuntimeError("route /prompt failed")

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        release_fetcher=lambda: _async_releases([_release("v0.20.1")]),
        archive_downloader=downloader,
        command_runner=_fake_command,
        smoke_tester=smoke,
        log_store=LogStore(),
    )

    await service._run_update("v0.20.1", "job-1")
    status = service.update_status()
    versions = await service.versions()

    assert status.status == "failed"
    assert "route /prompt failed" in (status.error or "")
    assert manager.repo_dir == original_repo
    failed = next(option for option in versions.options if option.tag == "v0.20.1")
    assert failed.status == "Failed validation"
    assert failed.failed_reason == "route /prompt failed"
    assert versions.current is None


@pytest.mark.anyio
async def test_missing_managed_env_triggers_staged_repair_on_start_failure(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('comfy')\n", encoding="utf-8")
    (source / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    record = LocalComfyUIVersionRecord(
        tag="v0.20.1",
        available_upstream=True,
        installed=True,
        active=True,
        locally_verified=True,
        source_path=str(source),
        env_path=str(paths.core_envs_dir / "comfyui-core-v0.20.1-test" / "venv"),
        archive_url="https://example.test/v0.20.1.zip",
    )
    _write_active(paths, record)
    manager = FakeRepairManager(source)
    smoke_calls: list[tuple[Path, Path]] = []

    async def smoke(source_dir: Path, env_dir: Path, record):
        smoke_calls.append((source_dir, env_dir))

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_fake_command,
        smoke_tester=smoke,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )
    result = await service.repair_after_start_failure(
        failed, repair_reason="Runtime Python executable not found"
    )
    current = (await service.versions()).current

    assert result.status == "repair_completed_started"
    assert manager.reconfigured
    assert smoke_calls
    assert smoke_calls[0][1].is_relative_to(paths.install_transactions_dir)
    assert current is not None
    assert current.locally_verified
    assert current.repair_attempt_count == 0
    assert Path(current.env_path or "").is_relative_to(paths.core_envs_dir)


@pytest.mark.anyio
async def test_missing_required_import_triggers_staged_env_rebuild(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('comfy')\n", encoding="utf-8")
    (source / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    broken_env = paths.core_envs_dir / "broken" / "venv"
    broken_python = broken_env / "bin" / "python"
    broken_python.parent.mkdir(parents=True)
    broken_python.write_text("", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(broken_env),
        ),
    )
    manager = FakeRepairManager(source)

    async def command(command: list[str], cwd: Path | None) -> CommandResult:
        if command[1:3] == ["-m", "venv"]:
            venv = Path(command[3])
            python = venv / "bin" / "python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            return CommandResult(returncode=0)
        if command[1:2] == ["-c"] and str(broken_env) in command[0]:
            return CommandResult(returncode=1, stderr="No module named torch")
        return CommandResult(returncode=0)

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )

    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )
    result = await service.repair_after_start_failure(
        failed, repair_reason="Runtime Python is missing required imports: torch"
    )

    assert result.status == "repair_completed_started"
    current = (await service.versions()).current
    assert current is not None
    assert current.env_path != str(broken_env)
    assert Path(current.env_path or "").exists()


@pytest.mark.anyio
async def test_successful_redownload_repair_validates_and_activates(
    tmp_path: Path,
) -> None:
    source_archive = tmp_path / "source.zip"
    _source_zip(source_archive)
    expected_hash = _source_hash_for_zip(source_archive, tmp_path)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    manager = FakeRepairManager(paths.core_engines_dir / "missing")

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(source_archive.read_bytes())
        return dest.stat().st_size

    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_hash=expected_hash,
            source_path=str(paths.core_engines_dir / "missing"),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
            archive_url="https://example.test/v0.20.1.zip",
        ),
    )
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        archive_downloader=downloader,
        command_runner=_fake_command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )

    failed = ProcessActionResult(status="repo_missing", comfyui=await manager.status())
    result = await service.repair_after_start_failure(
        failed, repair_reason="repo missing"
    )
    current = (await service.versions()).current

    assert result.status == "repair_completed_started"
    assert current is not None
    assert current.source_hash == expected_hash
    assert current.source_path is not None and Path(current.source_path).is_relative_to(
        paths.core_engines_dir
    )
    assert current.env_path is not None and Path(current.env_path).is_relative_to(
        paths.core_envs_dir
    )


@pytest.mark.anyio
async def test_repair_retry_policy_blocks_repeated_automatic_attempts(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    record = LocalComfyUIVersionRecord(
        tag="v0.20.1",
        installed=True,
        active=True,
        locally_verified=True,
        source_path=str(source),
        env_path=str(paths.core_envs_dir / "missing" / "venv"),
        repair_attempt_count=2,
        last_repair_attempt_at=__import__("datetime")
        .datetime.now(__import__("datetime").UTC)
        .isoformat(),
    )
    _write_active(paths, record)
    manager = FakeRepairManager(source)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_fake_command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )

    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )
    result = await service.repair_after_start_failure(
        failed, repair_reason="missing env"
    )

    assert result.status == "repair_blocked"
    assert manager.start_calls == 0
    assert service.update_status().phase == "repair_blocked"
    assert (await service.versions()).current.repair_blocked_until is not None


@pytest.mark.anyio
async def test_repair_source_hash_mismatch_is_repair_failed_not_incompatible(
    tmp_path: Path,
) -> None:
    source_archive = tmp_path / "source.zip"
    _source_zip(source_archive)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    missing_source = paths.core_engines_dir / "missing-source"
    record = LocalComfyUIVersionRecord(
        tag="v0.20.1",
        available_upstream=True,
        installed=True,
        active=True,
        locally_verified=True,
        source_hash="sha256:not-the-downloaded-source",
        source_path=str(missing_source),
        env_path=str(paths.core_envs_dir / "missing" / "venv"),
        archive_url="https://example.test/v0.20.1.zip",
    )
    _write_active(paths, record)
    manager = FakeRepairManager(missing_source)

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(source_archive.read_bytes())
        return dest.stat().st_size

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        archive_downloader=downloader,
        command_runner=_fake_command,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(status="repo_missing", comfyui=await manager.status())
    result = await service.repair_after_start_failure(
        failed, repair_reason="repo missing"
    )
    current = (await service.versions()).current

    assert result.status == "repair_failed_no_fallback"
    assert current is not None
    assert current.repair_status == "repair_failed"
    assert current.incompatible is False
    assert "hash mismatch" in (current.last_repair_error or "")


@pytest.mark.anyio
async def test_dependency_install_failure_is_repair_failed_not_incompatible(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    (source / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
        ),
    )
    manager = FakeRepairManager(source)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_failing_pip_command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )

    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )
    result = await service.repair_after_start_failure(
        failed, repair_reason="missing env"
    )
    current = (await service.versions()).current

    assert result.status == "repair_failed_no_fallback"
    assert current is not None
    assert current.repair_status == "repair_failed"
    assert current.incompatible is False
    assert "bootstrap_failed" in (current.last_repair_error or "")


@pytest.mark.anyio
async def test_extraction_failure_is_repair_failed_not_incompatible(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(paths.core_engines_dir / "missing"),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
            archive_url="https://example.test/v0.20.1.zip",
        ),
    )
    manager = FakeRepairManager(paths.core_engines_dir / "missing")

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(b"not a zip")
        return dest.stat().st_size

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        archive_downloader=downloader,
        command_runner=_fake_command,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(status="repo_missing", comfyui=await manager.status())
    result = await service.repair_after_start_failure(
        failed, repair_reason="repo missing"
    )
    current = (await service.versions()).current

    assert result.status == "repair_failed_no_fallback"
    assert current is not None
    assert current.repair_status == "repair_failed"
    assert current.incompatible is False


@pytest.mark.anyio
async def test_repair_failure_uses_bundled_fallback_when_previous_unavailable(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    bundled_source = tmp_path / "bundled-comfyui"
    bundled_source.mkdir()
    (bundled_source / "main.py").write_text("", encoding="utf-8")
    bundled_python = paths.core_envs_dir / "bundled-comfyui" / "bin" / "python"
    bundled_python.parent.mkdir(parents=True)
    bundled_python.write_text("", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(paths.core_engines_dir / "missing"),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
            archive_url="https://example.test/v0.20.1.zip",
        ),
    )
    manager = FakeRepairManager(paths.core_engines_dir / "missing")
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        bundled_repo_dir=bundled_source,
        bundled_python_executable=str(bundled_python),
        archive_downloader=lambda *_: _raise_download_failure(),
        command_runner=_fake_command,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(status="repo_missing", comfyui=await manager.status())
    result = await service.repair_after_start_failure(
        failed, repair_reason="repo missing"
    )

    assert result.status == "repair_failed_fallback_active"
    assert service.update_status().fallback_version == "bundled"
    assert manager.repo_dir == bundled_source


@pytest.mark.anyio
async def test_startup_failed_repairs_only_when_error_looks_environment_related(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
        ),
    )
    manager = FakeRepairManager(source)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_fake_command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )
    generic = ProcessActionResult(
        status="startup_failed",
        comfyui=ComfyUIRuntimeStatus(
            mode="managed",
            reachable=False,
            base_url="http://127.0.0.1:9999",
            repo_dir=str(source),
            managed_process_running=False,
            error="ComfyUI process exited during startup with code 1",
        ),
    )

    result = await service.repair_after_start_failure(
        generic, repair_reason=generic.comfyui.error or ""
    )

    assert result is generic
    assert manager.start_calls == 0
    assert _start_failure_is_repairable(
        "startup_failed", "ModuleNotFoundError: No module named 'torch'"
    )
    assert not _start_failure_is_repairable(
        "startup_failed", "ComfyUI process exited during startup with code 1"
    )


@pytest.mark.anyio
async def test_smoke_behavior_failure_after_repair_marks_incompatible(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    (source / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(paths.core_envs_dir / "missing" / "venv"),
        ),
    )
    manager = FakeRepairManager(source)

    async def smoke(source_dir: Path, env_dir: Path, record):
        raise RuntimeError("ComfyUI smoke route failed: /prompt -> 404")

    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_fake_command,
        smoke_tester=smoke,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )
    result = await service.repair_after_start_failure(
        failed, repair_reason="missing env"
    )
    current = (await service.versions()).current

    assert result.status == "repair_failed_no_fallback"
    assert service.update_status().incompatible_version == "v0.20.1"
    assert current is not None
    assert current.incompatible
    assert current.repair_status == "incompatible"


@pytest.mark.anyio
async def test_repair_failure_uses_previous_active_fallback(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    broken_source = paths.core_engines_dir / "broken-source"
    broken_source.mkdir(parents=True)
    previous_source = paths.core_engines_dir / "previous-source"
    previous_source.mkdir(parents=True)
    (previous_source / "main.py").write_text("", encoding="utf-8")
    previous_env = paths.core_envs_dir / "previous" / "venv"
    previous_python = previous_env / "bin" / "python"
    previous_python.parent.mkdir(parents=True)
    previous_python.write_text("", encoding="utf-8")
    active = LocalComfyUIVersionRecord(
        tag="v0.20.1",
        installed=True,
        active=True,
        locally_verified=True,
        source_hash="sha256:missing",
        source_path=str(broken_source / "missing"),
        env_path=str(paths.core_envs_dir / "missing" / "venv"),
    )
    previous = LocalComfyUIVersionRecord(
        tag="v0.19.0",
        installed=True,
        active=False,
        locally_verified=True,
        source_path=str(previous_source),
        env_path=str(previous_env),
    )
    _write_active_with_previous(paths, active, previous)
    manager = FakeRepairManager(broken_source)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        archive_downloader=lambda *_: _raise_download_failure(),
        command_runner=_fake_command,
        log_store=LogStore(),
    )

    failed = ProcessActionResult(status="repo_missing", comfyui=await manager.status())
    result = await service.repair_after_start_failure(
        failed, repair_reason="repo missing"
    )

    assert result.status == "repair_failed_fallback_active"
    assert service.update_status().fallback_version == "v0.19.0"
    assert manager.repo_dir == previous_source


@pytest.mark.anyio
async def test_manual_rebuild_resets_repair_block_and_uses_fresh_env_path(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-test"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    (source / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    env = paths.core_envs_dir / "comfyui-core-v0.20.1-test" / "venv"
    python = env / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(env),
            repair_status="repair_blocked",
            repair_attempt_count=2,
            repair_blocked_until="2999-01-01T00:00:00+00:00",
        ),
    )
    manager = FakeRepairManager(source)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        command_runner=_fake_command,
        smoke_tester=lambda *_: _async_noop(),
        log_store=LogStore(),
    )

    status = await service.start_rebuild(ComfyUIRebuildRequest(version="current"))
    assert status.operation == "rebuild"
    while service.update_status().status == "running":
        await __import__("asyncio").sleep(0.01)

    current = (await service.versions()).current
    assert service.update_status().status == "completed"
    assert current is not None
    assert current.repair_attempt_count == 0
    assert current.repair_blocked_until is None
    assert current.env_path != str(env)
    assert Path(current.env_path or "").exists()


@pytest.mark.anyio
async def test_external_mode_never_auto_repairs(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    manager = FakeRepairManager(tmp_path)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="external",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        log_store=LogStore(),
    )
    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )

    result = await service.repair_after_start_failure(
        failed, repair_reason="missing env"
    )

    assert result is failed
    assert manager.start_calls == 0


@pytest.mark.anyio
async def test_developer_override_never_auto_repairs(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    manager = FakeRepairManager(tmp_path)
    service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=manager,  # type: ignore[arg-type]
        mode="managed",
        developer_override=True,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        log_store=LogStore(),
    )
    failed = ProcessActionResult(
        status="environment_not_ready", comfyui=await manager.status()
    )

    result = await service.repair_after_start_failure(
        failed, repair_reason="missing env"
    )

    assert result is failed
    assert manager.start_calls == 0


async def _async_noop(*args) -> None:
    return None


async def _raise_download_failure(*args) -> int:
    raise RuntimeError("network unavailable")


@pytest.mark.anyio
async def test_updates_disabled_for_external_mode_and_developer_override(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    external = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=_manager(tmp_path),
        mode="external",
        developer_override=False,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        release_fetcher=lambda: _async_releases([]),
        log_store=LogStore(),
    )
    override = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=_manager(tmp_path),
        mode="managed",
        developer_override=True,
        bootstrap_python_executable="python3",
        torch_cuda_index_url=None,
        torch_cpu_index_url="https://download.pytorch.org/whl/cpu",
        release_fetcher=lambda: _async_releases([]),
        log_store=LogStore(),
    )

    assert not (await external.versions()).updates_allowed
    assert (
        await external.start_update(ComfyUIUpdateRequest(version="latest"))
    ).status == "blocked"
    assert not (await override.versions()).updates_allowed


def test_resolve_active_runtime_selection_uses_installed_metadata(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "comfyui-core-v0.20.1-hash"
    env = paths.core_envs_dir / "comfyui-core-v0.20.1-hash" / "venv"
    source.mkdir(parents=True)
    (source / "main.py").write_text("", encoding="utf-8")
    python = env / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    active = {
        "schema_version": "0.1.0",
        "active": {
            "tag": "v0.20.1",
            "available_upstream": True,
            "installed": True,
            "active": True,
            "locally_verified": True,
            "failed_validation": False,
            "failed_reason": None,
            "source_hash": "sha256:abc",
            "commit_sha": "abc",
            "source_path": str(source),
            "env_path": str(env),
            "archive_url": None,
            "installed_at": None,
            "activated_at": None,
            "validated_at": None,
        },
    }
    (paths.core_engines_dir / "active-comfyui.json").write_text(
        __import__("json").dumps(active), encoding="utf-8"
    )

    selection = resolve_active_runtime_selection(
        paths,
        fallback_repo_dir=tmp_path / "fallback",
        fallback_python_executable=None,
        mode="managed",
        developer_override=False,
    )

    assert selection.repo_dir == source
    assert selection.venv_dir == env
    assert selection.version_metadata.active_tag == "v0.20.1"


def test_resolve_active_runtime_selection_keeps_broken_installed_runtime_for_repair(
    tmp_path: Path,
) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    source = paths.core_engines_dir / "missing-source"
    env = paths.core_envs_dir / "missing-env" / "venv"
    _write_active(
        paths,
        LocalComfyUIVersionRecord(
            tag="v0.20.1",
            installed=True,
            active=True,
            locally_verified=True,
            source_path=str(source),
            env_path=str(env),
        ),
    )

    selection = resolve_active_runtime_selection(
        paths,
        fallback_repo_dir=tmp_path / "fallback",
        fallback_python_executable=None,
        mode="managed",
        developer_override=False,
    )

    assert selection.repo_dir == source
    assert selection.venv_dir == env
    assert selection.version_metadata.source_kind == "installed"


def test_repair_source_recovery_prefers_commit_then_recorded_archive_then_tag() -> None:
    record = LocalComfyUIVersionRecord(
        tag="v0.20.1",
        commit_sha="0123456789abcdef",
        archive_url="https://example.test/recorded.zip",
    )

    candidates = _archive_recovery_candidates(record)

    assert candidates == [
        "https://github.com/Comfy-Org/ComfyUI/archive/0123456789abcdef.zip",
        "https://example.test/recorded.zip",
        "https://github.com/Comfy-Org/ComfyUI/archive/refs/tags/v0.20.1.zip",
    ]


@pytest.mark.anyio
async def test_passive_runtime_status_does_not_trigger_repair(tmp_path: Path) -> None:
    class RuntimeStatusOnlyManager:
        async def status(self) -> ComfyUIRuntimeStatus:
            return ComfyUIRuntimeStatus(
                mode="managed",
                reachable=False,
                base_url="http://127.0.0.1:9999",
                repo_dir=str(tmp_path),
                managed_process_running=False,
                error="Runtime Python executable not found",
            )

    class RepairShouldNotRun:
        called = False

        async def repair_after_start_failure(self, *args, **kwargs):
            self.called = True
            raise AssertionError("passive status must not repair")

    repair = RepairShouldNotRun()
    service = EngineService(
        workflow_loader=None,  # type: ignore[arg-type]
        workflow_validator=None,  # type: ignore[arg-type]
        runner_supervisor=None,  # type: ignore[arg-type]
        runtime_manager=RuntimeStatusOnlyManager(),  # type: ignore[arg-type]
        log_store=LogStore(),
        comfyui_update_service=repair,  # type: ignore[arg-type]
    )

    status = await service.runtime_status()

    assert not status.reachable
    assert not repair.called


def test_required_api_route_status_checks_reject_missing_non_view_routes() -> None:
    assert _required_route_status_usable("/object_info", 200)
    assert not _required_route_status_usable("/object_info", 404)
    assert not _required_route_status_usable("/prompt", 405)
    assert _required_route_status_usable("/view", 404)
    assert not _required_route_status_usable("/view", 405)
    assert not _required_route_status_usable("/view", 500)
