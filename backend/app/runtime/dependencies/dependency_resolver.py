"""Resolve dependency marker files into Noofy dependency locks.

The resolver uses `uv pip compile` for version resolution, then materializes
hash-matched wheels into Noofy's shared wheel cache. It never imports custom
node modules or executes setup code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.accelerator_policy import ignored_dependency_reason
from app.runtime.dependencies.dependency_lock import (
    DependencyDeclaration,
    DependencyPolicyError,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    ResolverMetadata,
    inspect_dependency_marker_files,
    normalize_package_name,
    validate_quarantined_community_lock,
    with_computed_lock_hash,
)
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
        uv_cache_dir: Path | None = None,
        uv_executable: str = "uv",
        command_runner: _CommandRunner | None = None,
    ) -> None:
        self.wheel_cache_dir = wheel_cache_dir
        self.work_dir = work_dir
        self.package_index_client = package_index_client or PyPIPackageIndexClient()
        self.uv_cache_dir = uv_cache_dir
        self.uv_executable = uv_executable
        self.command_runner = command_runner or _run_command
        self.log_store = log_store

    def resolve(self, request: DependencyResolutionRequest) -> ResolvedDependencyLock:
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
        transaction_dir = (
            self.work_dir
            / f"dep-resolve-{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
        )
        try:
            transaction_dir.mkdir(parents=True)
            input_path = transaction_dir / "requirements.in"
            compiled_path = transaction_dir / "requirements.lock"
            input_path.write_text(
                "\n".join(declaration.requirement for declaration in declarations)
                + "\n",
                encoding="utf-8",
            )
            resolver = self._resolver_metadata(transaction_dir)
            if declarations:
                self._compile_requirements(
                    input_path,
                    compiled_path,
                    request=request,
                    cwd=transaction_dir,
                )
                requirements = parse_uv_compiled_requirements(
                    compiled_path.read_text(encoding="utf-8")
                )
            else:
                requirements = []
            requirements, transitive_ignored = _partition_supported_requirements(
                requirements,
                allowed_accelerator_packages=allowed_accelerators,
            )
            ignored.extend(transitive_ignored)
            self._record_ignored_dependencies(request, ignored)
            wheels = [
                self._wheel_from_requirement(
                    replace(
                        requirement,
                        python_version=request.python_version,
                        python_platform=request.python_platform,
                    ),
                    direct_names=direct_names,
                    resolver=resolver,
                )
                for requirement in requirements
            ]
            lock = with_computed_lock_hash(
                ResolvedDependencyLock(
                    runtime_profile_id=request.runtime_profile_id,
                    runtime_profile_variant_id=request.runtime_profile_variant_id,
                    runtime_profile_manifest_hash=request.runtime_profile_manifest_hash,
                    install_policy_version=request.install_policy_version,
                    source_policy=request.source_policy,
                    resolver=resolver,
                    wheels=wheels,
                )
            )
            validate_quarantined_community_lock(
                lock, wheel_cache_dir=self.wheel_cache_dir
            )
            return lock
        finally:
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
        result = self._run([self.uv_executable, "--version"], cwd=cwd)
        parts = (result.stdout or "").strip().split()
        version = parts[1] if len(parts) >= 2 else "unknown"
        return ResolverMetadata(
            name="uv", version=version, command="uv pip compile --generate-hashes"
        )

    def _compile_requirements(
        self,
        input_path: Path,
        compiled_path: Path,
        *,
        request: DependencyResolutionRequest,
        cwd: Path,
    ) -> None:
        python_version = ".".join(request.python_version.split(".")[:2])
        command = [
            self.uv_executable,
            "pip",
            "compile",
            str(input_path),
            "--generate-hashes",
            "--only-binary",
            ":all:",
            "--python-version",
            python_version,
            "--output-file",
            str(compiled_path),
            "--no-progress",
            *self._uv_cache_args(),
        ]
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
        self._run(command, cwd=cwd)

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
        self, command: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        result = self.command_runner(command, cwd=cwd, env=self._command_env())
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            message = (
                output.splitlines()[0]
                if output
                else f"uv failed with exit code {result.returncode}"
            )
            raise DependencyResolutionError(
                DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                f"Dependency resolution failed: {message}",
            )
        return result

    def _command_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["UV_NO_PROGRESS"] = "1"
        env["UV_NO_PYTHON_DOWNLOADS"] = "1"
        if self.uv_cache_dir is not None:
            env["UV_CACHE_DIR"] = str(self.uv_cache_dir)
        return env

    def _uv_cache_args(self) -> list[str]:
        if self.uv_cache_dir is None:
            return []
        return ["--cache-dir", str(self.uv_cache_dir)]


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
        with urllib.request.urlopen(metadata_url, timeout=30) as response:
            metadata = json.loads(response.read().decode("utf-8"))
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
