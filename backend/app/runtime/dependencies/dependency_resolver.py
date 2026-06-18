"""Resolve custom-node dependency declarations without importing node code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
import urllib.parse
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Protocol

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.accelerator_policy import (
    BUILD_BLOCKED_PACKAGES,
    ignored_dependency_reason,
    runtime_excluded_packages,
)
from app.runtime.dependencies.dependency_lock import (
    DEFAULT_APPROVED_INDEX_URL,
    DependencyDeclaration,
    DependencyDistributionKind,
    DependencyPolicyError,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyRequirement,
    ResolvedDependencyWheel,
    ResolverMetadata,
    dependency_declaration_uses_unsupported_source,
    inspect_dependency_marker_files,
    normalize_package_name,
    validate_quarantined_community_lock,
    with_computed_lock_hash,
)
from app.runtime.runtime_tool_versions import SUPPORTED_UV_VERSION
from app.source_policy import SourcePolicy

_PINNED_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^;\s]+)(?:\s*;\s*(?P<marker>.+?))?(?:\s+--hash=.+)?$"
)
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")


class DependencyResolutionError(RuntimeError):
    def __init__(self, code: DependencyPolicyErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResolvedRequirement:
    name: str
    version: str
    hashes: list[str]
    environment_marker: str | None = None
    python_version: str | None = None
    python_platform: str | None = None


@dataclass(frozen=True)
class MaterializedWheel:
    name: str
    version: str
    wheel_filename: str
    sha256: str
    approved_cache_ref: str
    source_index_url: str
    platform_tags: list[str]
    import_names: list[str]


@dataclass(frozen=True)
class DistributionCandidate:
    kind: DependencyDistributionKind
    filename: str
    url: str
    sha256: str
    source_index_url: str
    import_names: list[str]
    build_system_requires: list[str]
    legacy_setuptools_build: bool
    dynamic_build_requirements_possible: bool


@dataclass(frozen=True)
class DependencyResolutionRequest:
    source_dirs: list[Path]
    runtime_profile_id: str
    runtime_profile_variant_id: str
    runtime_profile_manifest_hash: str
    install_policy_version: str
    python_version: str
    python_platform: str | None
    workflow_id: str
    source_policy: SourcePolicy | None = None
    transaction_dir: Path | None = None
    approved_index_url: str = DEFAULT_APPROVED_INDEX_URL
    resolution_cutoff: str | None = None
    # Normalized accelerator package names a trusted runtime profile explicitly
    # pins and allows. Nothing populates this today; it exists so a future
    # validated profile can opt in without changing the stripping policy.
    allowed_accelerator_packages: tuple[str, ...] = ()


class PackageIndexClient(Protocol):
    def materialize_wheel(
        self,
        requirement: ResolvedRequirement,
        *,
        wheel_cache_dir: Path,
    ) -> MaterializedWheel:
        """Return a hash-verified wheel in wheel_cache_dir."""

    def inspect_distribution(
        self,
        requirement: ResolvedRequirement,
        *,
        work_dir: Path,
    ) -> DistributionCandidate:
        """Return the selected registry distribution and static build metadata."""


class _CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


class UvDependencyLockResolver:
    def __init__(
        self,
        *,
        wheel_cache_dir: Path,
        work_dir: Path,
        log_store: DiagnosticsSink,
        package_index_client: PackageIndexClient | None = None,
        uv_executable: str = "uv",
        command_runner: _CommandRunner | None = None,
    ) -> None:
        self.wheel_cache_dir = wheel_cache_dir
        self.work_dir = work_dir
        self.package_index_client = package_index_client or PyPIPackageIndexClient()
        self.uv_executable = uv_executable
        self.command_runner = command_runner or _run_command
        self.log_store = log_store

    def resolve(self, request: DependencyResolutionRequest) -> ResolvedDependencyLock:
        if request.approved_index_url != DEFAULT_APPROVED_INDEX_URL:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
                "Workflow dependencies must use Noofy's approved package index.",
            )
        allowed_accelerators = frozenset(request.allowed_accelerator_packages)
        declarations = self._discover_declarations(request.source_dirs)
        declarations, ignored = _partition_supported_declarations(
            declarations,
            allowed_accelerator_packages=allowed_accelerators,
        )
        direct_names = {
            _requirement_name(declaration.requirement) for declaration in declarations
        }
        direct_names.discard(None)
        transaction_dir = request.transaction_dir or (
            self.work_dir
            / f"dep-resolve-{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
        )
        remove_transaction_dir = request.transaction_dir is None
        try:
            transaction_dir.mkdir(parents=True, exist_ok=True)
            input_path = transaction_dir / "requirements.in"
            compiled_path = transaction_dir / "requirements.lock"
            excludes_path = transaction_dir / "runtime-excludes.txt"
            build_input_path = transaction_dir / "build-requirements.in"
            build_constraints_path = transaction_dir / "build-constraints.txt"
            runtime_excludes = list(
                runtime_excluded_packages(
                    allowed_accelerator_packages=allowed_accelerators
                )
            )
            excludes_text = "\n".join(runtime_excludes) + "\n"
            excludes_path.write_text(excludes_text, encoding="utf-8")
            input_path.write_text(
                "\n".join(declaration.requirement for declaration in declarations)
                + "\n",
                encoding="utf-8",
            )
            resolver = self._resolver_metadata(transaction_dir)
            cutoff = request.resolution_cutoff or _daily_resolution_cutoff()
            if declarations:
                self._compile_requirements(
                    input_path,
                    compiled_path,
                    excludes_path=excludes_path,
                    request=request,
                    cwd=transaction_dir,
                    resolution_cutoff=cutoff,
                )
                requirements, candidates, build_constraints = (
                    self._resolve_build_constraints(
                        input_path=input_path,
                        compiled_path=compiled_path,
                        excludes_path=excludes_path,
                        build_input_path=build_input_path,
                        build_constraints_path=build_constraints_path,
                        request=request,
                        cwd=transaction_dir,
                        resolution_cutoff=cutoff,
                    )
                )
            else:
                requirements = []
                candidates = {}
                build_constraints = ""
            unexpected_excluded = sorted(
                {
                    normalize_package_name(requirement.name)
                    for requirement in requirements
                    if normalize_package_name(requirement.name)
                    in set(runtime_excludes)
                }
            )
            if unexpected_excluded:
                raise DependencyResolutionError(
                    DependencyPolicyErrorCode.DEPENDENCY_OVERLAY_VALIDATION_FAILED,
                    "Protected dependencies remained in the compiled lock: "
                    + ", ".join(unexpected_excluded),
                )
            requirements, transitive_ignored = _partition_supported_requirements(
                requirements,
                allowed_accelerator_packages=allowed_accelerators,
            )
            ignored.extend(transitive_ignored)
            self._record_ignored_dependencies(request, ignored)
            resolved_requirements = [
                self._lock_requirement(
                    requirement,
                    candidate=candidates[normalize_package_name(requirement.name)],
                    direct_names=direct_names,
                )
                for requirement in requirements
            ]
            compiled_text = (
                compiled_path.read_text(encoding="utf-8")
                if compiled_path.exists()
                else ""
            )
            lock = with_computed_lock_hash(
                ResolvedDependencyLock(
                    runtime_profile_id=request.runtime_profile_id,
                    runtime_profile_variant_id=request.runtime_profile_variant_id,
                    runtime_profile_manifest_hash=request.runtime_profile_manifest_hash,
                    python_version=request.python_version,
                    python_platform=request.python_platform,
                    install_policy_version=request.install_policy_version,
                    source_policy=request.source_policy,
                    resolver=resolver,
                    requirements=resolved_requirements,
                    requirements_lock=compiled_text,
                    requirements_lock_hash=_text_hash(compiled_text),
                    runtime_excludes=runtime_excludes,
                    runtime_excludes_hash=_text_hash(excludes_text),
                    build_constraints=build_constraints,
                    build_constraints_hash=_text_hash(build_constraints),
                    source_distributions=sorted(
                        requirement.name
                        for requirement in resolved_requirements
                        if requirement.distribution_kind
                        is DependencyDistributionKind.SDIST
                    ),
                    resolution_cutoff=cutoff,
                    approved_index_url=request.approved_index_url,
                    ignored_dependencies=ignored,
                    build_requirements_complete=not any(
                        requirement.dynamic_build_requirements_possible
                        for requirement in resolved_requirements
                    ),
                    wheels=[],
                )
            )
            validate_quarantined_community_lock(lock)
            return lock
        finally:
            if remove_transaction_dir:
                shutil.rmtree(transaction_dir, ignore_errors=True)

    def _record_ignored_dependencies(
        self,
        request: DependencyResolutionRequest,
        ignored: list[dict[str, str | None]],
    ) -> None:
        if not ignored:
            return
        self.log_store.add(
            "info",
            "Skipped custom-node dependencies that are not installable in the stable runtime",
            "runtime.dependency_resolver",
            workflow_id=request.workflow_id,
            details={
                "runtime_profile_id": request.runtime_profile_id,
                "runtime_profile_variant_id": request.runtime_profile_variant_id,
                "ignored_dependencies": ignored,
            },
        )

    def _discover_declarations(
        self, source_dirs: list[Path]
    ) -> list[DependencyDeclaration]:
        declarations: list[DependencyDeclaration] = []
        findings = []
        for source_dir in source_dirs:
            inspection = inspect_dependency_marker_files(source_dir)
            declarations.extend(inspection.declarations)
            findings.extend(inspection.findings)
        if findings:
            finding = findings[0]
            raise DependencyResolutionError(finding.code, finding.message)
        return declarations

    def _resolver_metadata(self, cwd: Path) -> ResolverMetadata:
        result = self._run(
            [self.uv_executable, "--version"], cwd=cwd, phase="uv-version"
        )
        parts = (result.stdout or "").strip().split()
        version = parts[1] if len(parts) >= 2 else "unknown"
        if version != SUPPORTED_UV_VERSION:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.UNSUPPORTED_UV_VERSION,
                "Noofy's workflow dependency tool is out of date. Update Noofy and try again.",
            )
        return ResolverMetadata(
            name="uv",
            version=version,
            command="uv pip compile --generate-hashes --excludes",
        )

    def _compile_requirements(
        self,
        input_path: Path,
        compiled_path: Path,
        *,
        excludes_path: Path,
        request: DependencyResolutionRequest,
        cwd: Path,
        resolution_cutoff: str,
        build_constraints_path: Path | None = None,
    ) -> None:
        python_version = ".".join(request.python_version.split(".")[:2])
        command = [
            self.uv_executable,
            "pip",
            "compile",
            str(input_path),
            "--generate-hashes",
            "--excludes",
            str(excludes_path.resolve()),
            "--python-version",
            python_version,
            "--default-index",
            request.approved_index_url,
            "--no-sources",
            "--no-config",
            "--exclude-newer",
            resolution_cutoff,
            "--output-file",
            str(compiled_path),
            "--no-progress",
        ]
        if build_constraints_path is not None and build_constraints_path.exists():
            command.extend(
                ["--build-constraints", str(build_constraints_path.resolve())]
            )
        if request.python_platform:
            command.extend(["--python-platform", request.python_platform])
        self.log_store.add(
            "info",
            "Resolving dependency lock",
            "runtime.dependency_resolver",
            workflow_id=request.workflow_id,
            details={
                "source_dir_count": len(request.source_dirs),
                "python_version": python_version,
            },
        )
        self._run(command, cwd=cwd, phase="runtime-compile")

    def _resolve_build_constraints(
        self,
        *,
        input_path: Path,
        compiled_path: Path,
        excludes_path: Path,
        build_input_path: Path,
        build_constraints_path: Path,
        request: DependencyResolutionRequest,
        cwd: Path,
        resolution_cutoff: str,
    ) -> tuple[
        list[ResolvedRequirement],
        dict[str, DistributionCandidate],
        str,
    ]:
        previous_sdist_selection: set[tuple[str, str, str, str]] | None = None
        candidate_cache: dict[
            tuple[str, str, tuple[str, ...], str, str | None],
            DistributionCandidate,
        ] = {}
        for attempt in range(1, 4):
            requirements = parse_uv_compiled_requirements(
                compiled_path.read_text(encoding="utf-8")
            )
            candidates = self._inspect_candidates(
                requirements,
                request=request,
                cwd=cwd,
                candidate_cache=candidate_cache,
            )
            sdist_selection = _sdist_selection(requirements, candidates)
            build_requirements = sorted(
                {
                    requirement
                    for candidate in candidates.values()
                    for requirement in candidate.build_system_requires
                }
            )
            _validate_build_requirements(build_requirements)
            build_constraints = self._compile_build_constraints(
                build_requirements,
                input_path=build_input_path,
                output_path=build_constraints_path,
                request=request,
                cwd=cwd,
                resolution_cutoff=resolution_cutoff,
            )
            if (
                previous_sdist_selection is not None
                and sdist_selection == previous_sdist_selection
            ):
                return requirements, candidates, build_constraints
            if not sdist_selection:
                return requirements, candidates, build_constraints
            previous_sdist_selection = sdist_selection
            self._compile_requirements(
                input_path,
                compiled_path,
                excludes_path=excludes_path,
                request=request,
                cwd=cwd,
                resolution_cutoff=resolution_cutoff,
                build_constraints_path=build_constraints_path,
            )
        final_requirements = parse_uv_compiled_requirements(
            compiled_path.read_text(encoding="utf-8")
        )
        final_candidates = self._inspect_candidates(
            final_requirements,
            request=request,
            cwd=cwd,
            candidate_cache=candidate_cache,
        )
        final_sdist_selection = _sdist_selection(
            final_requirements, final_candidates
        )
        if final_sdist_selection != previous_sdist_selection:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.DEPENDENCY_BUILD_RESOLUTION_UNSTABLE,
                "Workflow dependency source-build selection did not stabilize.",
            )
        return (
            final_requirements,
            final_candidates,
            build_constraints_path.read_text(encoding="utf-8")
            if build_constraints_path.exists()
            else "",
        )

    def _inspect_candidates(
        self,
        requirements: list[ResolvedRequirement],
        *,
        request: DependencyResolutionRequest,
        cwd: Path,
        candidate_cache: dict[
            tuple[str, str, tuple[str, ...], str, str | None],
            DistributionCandidate,
        ],
    ) -> dict[str, DistributionCandidate]:
        candidates: dict[str, DistributionCandidate] = {}
        for requirement in requirements:
            targeted = replace(
                requirement,
                python_version=request.python_version,
                python_platform=request.python_platform,
            )
            cache_key = (
                normalize_package_name(targeted.name),
                targeted.version,
                tuple(sorted(targeted.hashes)),
                request.python_version,
                request.python_platform,
            )
            cached = candidate_cache.get(cache_key)
            if cached is not None:
                candidates[normalize_package_name(requirement.name)] = cached
                continue
            inspect_distribution = getattr(
                self.package_index_client, "inspect_distribution", None
            )
            try:
                if inspect_distribution is not None:
                    candidate = inspect_distribution(targeted, work_dir=cwd)
                else:
                    wheel = self.package_index_client.materialize_wheel(
                        targeted, wheel_cache_dir=self.wheel_cache_dir
                    )
                    candidate = DistributionCandidate(
                        kind=DependencyDistributionKind.WHEEL,
                        filename=wheel.wheel_filename,
                        url=wheel.source_index_url,
                        sha256=wheel.sha256,
                        source_index_url=DEFAULT_APPROVED_INDEX_URL,
                        import_names=wheel.import_names,
                        build_system_requires=[],
                        legacy_setuptools_build=False,
                        dynamic_build_requirements_possible=False,
                    )
            except DependencyResolutionError as exc:
                _write_resolution_failure(
                    cwd,
                    package=targeted.name,
                    code=exc.code,
                    error=str(exc),
                )
                raise
            except DependencyPolicyError as exc:
                _write_resolution_failure(
                    cwd,
                    package=targeted.name,
                    code=exc.code,
                    error=str(exc),
                )
                raise
            except Exception as exc:
                _write_resolution_failure(
                    cwd,
                    package=targeted.name,
                    code=DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
                    error=repr(exc),
                )
                raise DependencyResolutionError(
                    DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
                    f"Package index inspection failed for {targeted.name}: {exc}",
                ) from exc
            candidate_cache[cache_key] = candidate
            candidates[normalize_package_name(requirement.name)] = candidate
        _write_distribution_diagnostics(cwd, candidates)
        return candidates

    def _compile_build_constraints(
        self,
        requirements: list[str],
        *,
        input_path: Path,
        output_path: Path,
        request: DependencyResolutionRequest,
        cwd: Path,
        resolution_cutoff: str,
    ) -> str:
        if not requirements:
            output_path.write_text("", encoding="utf-8")
            return ""
        input_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        command = [
            self.uv_executable,
            "pip",
            "compile",
            str(input_path),
            "--generate-hashes",
            "--python-version",
            ".".join(request.python_version.split(".")[:2]),
            "--default-index",
            request.approved_index_url,
            "--no-sources",
            "--no-config",
            "--exclude-newer",
            resolution_cutoff,
            "--output-file",
            str(output_path),
            "--no-progress",
        ]
        if request.python_platform:
            command.extend(["--python-platform", request.python_platform])
        self._run(command, cwd=cwd, phase="build-constraints")
        contents = output_path.read_text(encoding="utf-8")
        resolved = parse_uv_compiled_requirements(contents)
        blocked = sorted(
            {
                normalize_package_name(requirement.name)
                for requirement in resolved
                if normalize_package_name(requirement.name)
                in BUILD_BLOCKED_PACKAGES
            }
        )
        if blocked:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.DEPENDENCY_BUILD_POLICY_BLOCKED,
                "A workflow extension requires unsupported build tooling: "
                + ", ".join(blocked),
            )
        return contents

    def _lock_requirement(
        self,
        requirement: ResolvedRequirement,
        *,
        candidate: DistributionCandidate,
        direct_names: set[str | None],
    ) -> ResolvedDependencyRequirement:
        relationship = (
            DependencyRelationship.DIRECT
            if normalize_package_name(requirement.name) in direct_names
            else DependencyRelationship.TRANSITIVE
        )
        return ResolvedDependencyRequirement(
            name=requirement.name,
            version=requirement.version,
            hashes=requirement.hashes,
            environment_marker=requirement.environment_marker,
            relationship=relationship,
            requested_by=(
                ["dependency-marker"]
                if relationship is DependencyRelationship.DIRECT
                else []
            ),
            distribution_kind=candidate.kind,
            distribution_filename=candidate.filename,
            distribution_url=candidate.url,
            distribution_sha256=candidate.sha256,
            source_index_url=candidate.source_index_url,
            import_names=candidate.import_names,
            build_system_requires=candidate.build_system_requires,
            legacy_setuptools_build=candidate.legacy_setuptools_build,
            dynamic_build_requirements_possible=(
                candidate.dynamic_build_requirements_possible
            ),
        )

    def _wheel_from_requirement(
        self,
        requirement: ResolvedRequirement,
        *,
        direct_names: set[str | None],
        resolver: ResolverMetadata,
    ) -> ResolvedDependencyWheel:
        wheel = self.package_index_client.materialize_wheel(
            requirement,
            wheel_cache_dir=self.wheel_cache_dir,
        )
        relationship = (
            DependencyRelationship.DIRECT
            if normalize_package_name(requirement.name) in direct_names
            else DependencyRelationship.TRANSITIVE
        )
        return ResolvedDependencyWheel(
            name=requirement.name,
            version=requirement.version,
            wheel_filename=wheel.wheel_filename,
            sha256=wheel.sha256,
            source_kind=DependencySourceKind.APPROVED_CACHE,
            source_index_url=wheel.source_index_url,
            approved_cache_ref=wheel.approved_cache_ref,
            platform_tags=wheel.platform_tags,
            environment_marker=requirement.environment_marker,
            import_names=wheel.import_names,
            relationship=relationship,
            requested_by=(
                ["dependency-marker"]
                if relationship is DependencyRelationship.DIRECT
                else []
            ),
            resolver_name=resolver.name,
            resolver_version=resolver.version,
        )

    def _run(
        self, command: list[str], *, cwd: Path, phase: str
    ) -> subprocess.CompletedProcess[str]:
        result = self.command_runner(command, cwd=cwd, env=self._command_env(cwd))
        _write_command_output(cwd, phase, command, result)
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            message = (
                output.splitlines()[0]
                if output
                else f"uv failed with exit code {result.returncode}"
            )
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
                f"Dependency resolution failed: {message}",
            )
        return result

    def _command_env(self, cwd: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["UV_NO_PROGRESS"] = "1"
        env["UV_NO_PYTHON_DOWNLOADS"] = "1"
        transaction_root = cwd.parent if cwd.name == "dependency-resolution" else cwd
        cache_dir = transaction_root / "dependency-uv-cache"
        temp_dir = transaction_root / "dependency-build-tmp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        env["UV_CACHE_DIR"] = str(cache_dir)
        env["TMPDIR"] = str(temp_dir)
        env["TEMP"] = str(temp_dir)
        env["TMP"] = str(temp_dir)
        return env

class PyPIPackageIndexClient:
    def __init__(self, *, base_url: str = "https://pypi.org/pypi") -> None:
        self.base_url = base_url.rstrip("/")

    def materialize_wheel(
        self,
        requirement: ResolvedRequirement,
        *,
        wheel_cache_dir: Path,
    ) -> MaterializedWheel:
        metadata_url = f"{self.base_url}/{normalize_package_name(requirement.name)}/{requirement.version}/json"
        metadata = _read_package_index_metadata(
            metadata_url,
            package=requirement.name,
        )
        allowed_hashes = {
            hash_value.removeprefix("sha256:").lower()
            for hash_value in requirement.hashes
        }
        for file_record in metadata.get("urls", []):
            filename = file_record.get("filename", "")
            digest = file_record.get("digests", {}).get("sha256", "").lower()
            if (
                not filename.endswith(".whl")
                or digest not in allowed_hashes
                or not _wheel_matches_target(filename, requirement)
            ):
                continue
            wheel_cache_dir.mkdir(parents=True, exist_ok=True)
            target = wheel_cache_dir / filename
            if not target.exists():
                with urllib.request.urlopen(
                    file_record["url"], timeout=120
                ) as response:
                    target.write_bytes(response.read())
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual.lower() != digest:
                raise DependencyPolicyError(
                    DependencyPolicyErrorCode.HASH_MISMATCH,
                    f"Downloaded wheel hash mismatch for {requirement.name}.",
                )
            return MaterializedWheel(
                name=requirement.name,
                version=requirement.version,
                wheel_filename=filename,
                sha256=f"sha256:{digest}",
                approved_cache_ref=filename,
                source_index_url=metadata_url,
                platform_tags=_platform_tags_from_wheel(filename),
                import_names=_import_names_from_wheel(target),
            )
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.MISSING_WHEEL,
            f"No matching wheel found for {requirement.name}=={requirement.version}.",
        )

    def inspect_distribution(
        self,
        requirement: ResolvedRequirement,
        *,
        work_dir: Path,
    ) -> DistributionCandidate:
        metadata_url = (
            f"{self.base_url}/{normalize_package_name(requirement.name)}/"
            f"{requirement.version}/json"
        )
        metadata = _read_package_index_metadata(
            metadata_url,
            package=requirement.name,
        )
        allowed_hashes = {
            hash_value.removeprefix("sha256:").lower()
            for hash_value in requirement.hashes
        }
        matching_sdist: dict[str, object] | None = None
        for file_record in metadata.get("urls", []):
            filename = str(file_record.get("filename", ""))
            digest = str(
                file_record.get("digests", {}).get("sha256", "")
            ).lower()
            if digest not in allowed_hashes:
                continue
            if filename.endswith(".whl") and _wheel_matches_target(
                filename, requirement
            ):
                return DistributionCandidate(
                    kind=DependencyDistributionKind.WHEEL,
                    filename=filename,
                    url=str(file_record["url"]),
                    sha256=f"sha256:{digest}",
                    source_index_url=DEFAULT_APPROVED_INDEX_URL,
                    import_names=[],
                    build_system_requires=[],
                    legacy_setuptools_build=False,
                    dynamic_build_requirements_possible=False,
                )
            if file_record.get("packagetype") == "sdist":
                matching_sdist = file_record
        if matching_sdist is None:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
                f"No compatible registry distribution was found for "
                f"{requirement.name}=={requirement.version}.",
            )
        filename = str(matching_sdist["filename"])
        _validate_registry_artifact_identity(
            filename=filename,
            url=str(matching_sdist["url"]),
        )
        digest = str(matching_sdist["digests"]["sha256"]).lower()
        source_dir = work_dir / "source-archives"
        source_dir.mkdir(parents=True, exist_ok=True)
        archive_path = source_dir / filename
        if not archive_path.exists():
            _download_bounded(str(matching_sdist["url"]), archive_path)
        actual = _sha256_file(archive_path)
        if actual != digest or digest not in allowed_hashes:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.HASH_MISMATCH,
                f"Source archive hash mismatch for {requirement.name}.",
            )
        build_requires, legacy, dynamic = _inspect_build_system(archive_path)
        return DistributionCandidate(
            kind=DependencyDistributionKind.SDIST,
            filename=filename,
            url=str(matching_sdist["url"]),
            sha256=f"sha256:{digest}",
            source_index_url=DEFAULT_APPROVED_INDEX_URL,
            import_names=[],
            build_system_requires=build_requires,
            legacy_setuptools_build=legacy,
            dynamic_build_requirements_possible=dynamic,
        )


def parse_uv_compiled_requirements(contents: str) -> list[ResolvedRequirement]:
    requirements: list[ResolvedRequirement] = []
    logical_lines = _logical_requirement_lines(contents)
    for line in logical_lines:
        match = _PINNED_REQUIREMENT_RE.match(line)
        if not match:
            continue
        hashes = [f"sha256:{value.lower()}" for value in _HASH_RE.findall(line)]
        if not hashes:
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.MISSING_HASH,
                f"Resolved requirement is missing hashes: {line}",
            )
        requirements.append(
            ResolvedRequirement(
                name=match.group("name"),
                version=match.group("version"),
                hashes=hashes,
                environment_marker=match.group("marker"),
            )
        )
    return requirements


def _sdist_selection(
    requirements: list[ResolvedRequirement],
    candidates: dict[str, DistributionCandidate],
) -> set[tuple[str, str, str, str]]:
    selection: set[tuple[str, str, str, str]] = set()
    for requirement in requirements:
        name = normalize_package_name(requirement.name)
        candidate = candidates[name]
        if candidate.kind is DependencyDistributionKind.SDIST:
            selection.add(
                (
                    name,
                    requirement.version,
                    candidate.filename,
                    candidate.sha256,
                )
            )
    return selection


def custom_node_dependency_source_dirs(source_files_dir: Path) -> list[Path]:
    custom_nodes_dir = source_files_dir / "custom_nodes"
    if not custom_nodes_dir.is_dir():
        return []
    return sorted(path for path in custom_nodes_dir.iterdir() if path.is_dir())


def _logical_requirement_lines(contents: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw_line in contents.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("--") and not current:
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1].strip() + " "
            continue
        current += stripped
        if current:
            lines.append(current.strip())
        current = ""
    if current:
        lines.append(current.strip())
    return lines


def _requirement_name(requirement: str) -> str | None:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        return None
    return normalize_package_name(match.group(1))


def _partition_supported_declarations(
    declarations: list[DependencyDeclaration],
    *,
    allowed_accelerator_packages: frozenset[str],
) -> tuple[list[DependencyDeclaration], list[dict[str, str | None]]]:
    """Split direct declarations into supported ones and ignored-policy records."""
    supported: list[DependencyDeclaration] = []
    ignored: list[dict[str, str | None]] = []
    for declaration in declarations:
        name = _requirement_name(declaration.requirement)
        reason = (
            ignored_dependency_reason(
                name, allowed_accelerator_packages=allowed_accelerator_packages
            )
            if name is not None
            else None
        )
        if reason is None:
            supported.append(declaration)
            continue
        ignored.append(
            {
                "name": name,
                "requirement": declaration.requirement,
                "source_file": declaration.source_file,
                "reason": reason,
            }
        )
    return supported, ignored


def _partition_supported_requirements(
    requirements: list[ResolvedRequirement],
    *,
    allowed_accelerator_packages: frozenset[str],
) -> tuple[list[ResolvedRequirement], list[dict[str, str | None]]]:
    """Drop transitively resolved packages the stable runtime must not install."""
    supported: list[ResolvedRequirement] = []
    ignored: list[dict[str, str | None]] = []
    for requirement in requirements:
        name = normalize_package_name(requirement.name)
        reason = ignored_dependency_reason(
            name, allowed_accelerator_packages=allowed_accelerator_packages
        )
        if reason is None:
            supported.append(requirement)
            continue
        ignored.append(
            {
                "name": name,
                "requirement": f"{requirement.name}=={requirement.version}",
                "source_file": None,
                "reason": reason,
            }
        )
    return supported, ignored


def _platform_tags_from_wheel(filename: str) -> list[str]:
    stem = filename.removesuffix(".whl")
    parts = stem.split("-")
    if len(parts) < 5:
        return []
    return ["-".join(parts[-3:])]


def _wheel_matches_target(
    filename: str, requirement: ResolvedRequirement
) -> bool:
    stem = filename.removesuffix(".whl")
    parts = stem.split("-")
    if len(parts) < 5:
        return False
    python_tags = set(parts[-3].split("."))
    abi_tags = set(parts[-2].split("."))
    platform_tags = set(parts[-1].split("."))
    if not _python_tags_match(python_tags, abi_tags, requirement.python_version):
        return False
    return _platform_tags_match(platform_tags, requirement.python_platform)


def _python_tags_match(
    python_tags: set[str],
    abi_tags: set[str],
    python_version: str | None,
) -> bool:
    if python_version is None:
        return True
    major_minor = "".join(python_version.split(".")[:2])
    major = python_version.split(".", 1)[0]
    target_cp = f"cp{major_minor}"
    if target_cp in python_tags:
        return target_cp in abi_tags or "abi3" in abi_tags or "none" in abi_tags
    if "abi3" in abi_tags:
        target_version = int(major_minor)
        for tag in python_tags:
            if not tag.startswith("cp"):
                continue
            try:
                if int(tag.removeprefix("cp")) <= target_version:
                    return True
            except ValueError:
                continue
    return f"py{major}" in python_tags or "py3" in python_tags


def _platform_tags_match(
    platform_tags: set[str], python_platform: str | None
) -> bool:
    if python_platform is None or "any" in platform_tags:
        return True
    if python_platform == "x86_64-unknown-linux-gnu":
        return any(
            tag.endswith("_x86_64")
            and (
                tag.startswith("manylinux")
                or tag.startswith("musllinux")
                or tag == "linux_x86_64"
            )
            for tag in platform_tags
        )
    if python_platform == "aarch64-apple-darwin":
        return any(tag.startswith("macosx") and "arm64" in tag for tag in platform_tags)
    if python_platform == "x86_64-pc-windows-msvc":
        return "win_amd64" in platform_tags
    return True


def _import_names_from_wheel(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as wheel:
            names = wheel.namelist()
            for name in names:
                if name.endswith(".dist-info/top_level.txt"):
                    contents = wheel.read(name).decode("utf-8", errors="replace")
                    import_names = sorted(
                        {
                            line.strip()
                            for line in contents.splitlines()
                            if line.strip() and _is_valid_import_name(line.strip())
                        }
                    )
                    if import_names:
                        return import_names
            return _import_names_from_wheel_files(names)
    except zipfile.BadZipFile:
        return []
    return []


def _import_names_from_wheel_files(names: list[str]) -> list[str]:
    import_names: set[str] = set()
    for raw_name in names:
        parts = raw_name.split("/")
        if not parts:
            continue
        root = parts[0]
        if (
            not root
            or root.startswith(".")
            or root.endswith((".dist-info", ".data", ".libs"))
        ):
            continue
        if len(parts) == 1 and root.endswith(".py"):
            import_name = root[:-3]
        elif len(parts) > 1 and parts[1] == "__init__.py":
            import_name = root
        else:
            continue
        if _is_valid_import_name(import_name):
            import_names.add(import_name)
    return sorted(import_names)


def _is_valid_import_name(value: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$", value)
    )


def _daily_resolution_cutoff() -> str:
    today = datetime.now(UTC).date().isoformat()
    return f"{today}T23:59:59Z"


def _text_hash(contents: str) -> str:
    return "sha256:" + hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _validate_build_requirements(requirements: list[str]) -> None:
    blocked = sorted(
        {
            name
            for requirement in requirements
            if (name := _requirement_name(requirement)) in BUILD_BLOCKED_PACKAGES
        }
    )
    if blocked:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_POLICY_BLOCKED,
            "A workflow extension requires unsupported build tooling: "
            + ", ".join(blocked),
        )
    for requirement in requirements:
        if _build_requirement_is_unsafe(requirement):
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
                "A source package declared an unsupported build dependency.",
            )


def _build_requirement_is_unsafe(requirement: str) -> bool:
    return dependency_declaration_uses_unsupported_source(requirement)


def _read_package_index_metadata(
    url: str,
    *,
    package: str,
    max_bytes: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = response.read(max_bytes + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
            f"Package index metadata could not be downloaded for {package}: {exc}",
        ) from exc
    if len(payload) > max_bytes:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
            f"Package index metadata is too large for {package}.",
        )
    try:
        metadata = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
            f"Package index metadata is invalid for {package}: {exc}",
        ) from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("urls"), list):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED,
            f"Package index metadata is incomplete for {package}.",
        )
    return metadata


def _download_bounded(
    url: str,
    target: Path,
    *,
    max_bytes: int = 256 * 1024 * 1024,
) -> None:
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            with target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DependencyResolutionError(
                            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
                            "A workflow dependency source archive is too large to inspect.",
                        )
                    output.write(chunk)
    except DependencyResolutionError:
        target.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as exc:
        target.unlink(missing_ok=True)
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            f"Source package archive could not be downloaded: {exc}",
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_build_system(path: Path) -> tuple[list[str], bool, bool]:
    pyproject = _read_archive_member(path, "pyproject.toml")
    if pyproject is None:
        return ["setuptools>=40.8.0"], True, True
    try:
        data = tomllib.loads(pyproject.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            f"Source package has an invalid pyproject.toml: {exc}",
        ) from exc
    build_system = data.get("build-system")
    if not isinstance(build_system, dict):
        return ["setuptools>=40.8.0"], True, True
    if build_system.get("backend-path"):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_POLICY_BLOCKED,
            "Source package uses a local build backend, which Noofy does not allow.",
        )
    raw_requires = build_system.get("requires", [])
    if not isinstance(raw_requires, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_requires
    ):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            "Source package has invalid build-system requirements.",
        )
    requirements = [str(item).strip() for item in raw_requires]
    return requirements, False, True


def _read_archive_member(path: Path, filename: str) -> bytes | None:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if (
                    (parts := _safe_archive_parts(info.filename))[-1:]
                    == (filename,)
                    and len(parts) <= 2
                )
            ]
            if not matches:
                return None
            if len(matches) != 1 or matches[0].file_size > 1024 * 1024:
                raise DependencyResolutionError(
                    DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
                    "Source package build metadata is ambiguous or too large.",
                )
            return archive.read(matches[0])
    try:
        with tarfile.open(path, mode="r:*") as archive:
            matches = [
                member
                for member in archive.getmembers()
                if member.isfile()
                and (
                    (parts := _safe_archive_parts(member.name))[-1:]
                    == (filename,)
                    and len(parts) <= 2
                )
            ]
            if not matches:
                return None
            if len(matches) != 1 or matches[0].size > 1024 * 1024:
                raise DependencyResolutionError(
                    DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
                    "Source package build metadata is ambiguous or too large.",
                )
            extracted = archive.extractfile(matches[0])
            return extracted.read() if extracted is not None else None
    except tarfile.TarError as exc:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            "Source package archive could not be inspected.",
        ) from exc


def _validate_registry_artifact_identity(*, filename: str, url: str) -> None:
    if (
        not filename
        or filename in {".", ".."}
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
            "Package index returned an unsafe source archive filename.",
        )
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"files.pythonhosted.org", "pypi.org"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
            "Package index returned an unapproved source archive URL.",
        )


def _safe_archive_parts(name: str) -> tuple[str, ...]:
    if "\\" in name:
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            "Source package archive contains an unsafe path.",
        )
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DependencyResolutionError(
            DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED,
            "Source package archive contains an unsafe path.",
        )
    return path.parts


def _write_command_output(
    cwd: Path,
    phase: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> None:
    diagnostics_dir = cwd / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    safe_phase = re.sub(r"[^a-z0-9_.-]+", "-", phase.lower())
    (diagnostics_dir / f"{safe_phase}.command.json").write_text(
        json.dumps(
            {
                "command": [
                    part if len(part) < 240 else part[:237] + "..."
                    for part in command
                ],
                "exit_code": result.returncode,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (diagnostics_dir / f"{safe_phase}.stdout.log").write_text(
        result.stdout or "", encoding="utf-8"
    )
    (diagnostics_dir / f"{safe_phase}.stderr.log").write_text(
        result.stderr or "", encoding="utf-8"
    )


def _write_distribution_diagnostics(
    cwd: Path,
    candidates: dict[str, DistributionCandidate],
) -> None:
    diagnostics_dir = cwd / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "packages": [
            {
                "name": name,
                "distribution_kind": candidate.kind.value,
                "filename": candidate.filename,
                "url": candidate.url,
                "sha256": candidate.sha256,
                "source_index_url": candidate.source_index_url,
                "build_system_requires": candidate.build_system_requires,
                "legacy_setuptools_build": candidate.legacy_setuptools_build,
                "dynamic_build_requirements_possible": (
                    candidate.dynamic_build_requirements_possible
                ),
            }
            for name, candidate in sorted(candidates.items())
        ]
    }
    (diagnostics_dir / "source-distributions.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_resolution_failure(
    cwd: Path,
    *,
    package: str,
    code: DependencyPolicyErrorCode,
    error: str,
) -> None:
    diagnostics_dir = cwd / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / "distribution-inspection-failure.json").write_text(
        json.dumps(
            {
                "package": package,
                "error_code": code.value,
                "error": error,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
