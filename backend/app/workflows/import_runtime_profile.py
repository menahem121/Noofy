from __future__ import annotations

import platform
import subprocess

from app.runtime.profiles import RuntimeProfile, RuntimeProfileVariant


class RuntimeProfileSelectionError(RuntimeError):
    """Raised when the local machine has no supported import runtime profile."""


def select_import_runtime_profile(
    profiles: list[RuntimeProfile],
) -> tuple[RuntimeProfile, RuntimeProfileVariant]:
    """Select the local v1 runtime variant for imported package preparation."""
    if not profiles:
        raise RuntimeProfileSelectionError("Runtime profile catalog is empty.")
    profile = profiles[0]
    os_name = current_os_name()
    architecture = current_architecture()
    preferred_gpu = preferred_gpu_backend(os_name, architecture)
    for variant in profile.variants:
        if (
            variant.os == os_name
            and variant.architecture == architecture
            and variant.gpu_backend_profile == preferred_gpu
        ):
            return profile, variant
    for variant in profile.variants:
        if (
            variant.os == os_name
            and variant.architecture == architecture
            and variant.gpu_backend_profile == "cpu"
        ):
            return profile, variant
    raise RuntimeProfileSelectionError(
        f"No supported runtime profile variant for {os_name}/{architecture}."
    )


def current_os_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def current_architecture() -> str:
    machine = (platform.machine() or platform.processor() or "").lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    return machine or "unknown"


def preferred_gpu_backend(os_name: str, architecture: str) -> str:
    if os_name == "darwin" and architecture == "arm64":
        return "mps"
    if os_name == "darwin":
        return "unsupported"
    if os_name in {"linux", "windows"} and architecture == "x64" and has_nvidia_gpu():
        return "cuda"
    return "cpu"


def has_nvidia_gpu() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())
