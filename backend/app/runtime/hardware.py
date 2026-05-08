import platform
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.engine.models import RuntimeHardwareProfile, TorchInstallPlan

CommandRunner = Callable[[list[str], Any], Awaitable[Any]]

TORCH_PACKAGES = ["torch", "torchvision", "torchaudio"]
UNSUPPORTED_MACOS_INTEL_ACCELERATOR = "unsupported_macos_intel"


async def detect_hardware(command_runner: CommandRunner) -> RuntimeHardwareProfile:
    os_name = platform.system() or "Unknown"
    machine = platform.machine() or "unknown"
    architecture = platform.processor() or machine
    notes: list[str] = []

    if os_name == "Darwin":
        if machine.lower() in {"arm64", "aarch64"}:
            accelerator = "apple_mps"
            notes.append("Apple Silicon detected; PyTorch macOS wheels can use MPS when available.")
        else:
            accelerator = UNSUPPORTED_MACOS_INTEL_ACCELERATOR
            notes.append(
                "macOS Intel is unsupported for Noofy managed ComfyUI runtime. "
                "CPU/RAM diagnostics may work, but Noofy will not prepare a managed runtime."
            )
        return RuntimeHardwareProfile(
            os_name=os_name,
            os_version=platform.mac_ver()[0] or None,
            machine=machine,
            architecture=architecture,
            accelerator=accelerator,
            notes=notes,
        )

    nvidia_profile = await _detect_nvidia(command_runner, os_name, machine, architecture)
    if nvidia_profile is not None:
        return nvidia_profile

    notes.append("No NVIDIA GPU was detected; selecting a CPU PyTorch build.")
    return RuntimeHardwareProfile(
        os_name=os_name,
        os_version=platform.version() or None,
        machine=machine,
        architecture=architecture,
        accelerator="cpu",
        notes=notes,
    )


def plan_torch_install(
    hardware: RuntimeHardwareProfile,
    *,
    cuda_index_url: str | None = None,
    cpu_index_url: str = "https://download.pytorch.org/whl/cpu",
) -> TorchInstallPlan:
    if hardware.accelerator == "nvidia_cuda":
        selected_cuda_index_url = cuda_index_url or _select_cuda_index_url(hardware.cuda_version)
        if selected_cuda_index_url is None:
            return TorchInstallPlan(
                accelerator="cpu",
                packages=TORCH_PACKAGES,
                index_url=cpu_index_url,
                pip_args=["--index-url", cpu_index_url],
                reason="NVIDIA GPU was detected, but the reported CUDA capability is below the supported wheel policy; installing CPU-only PyTorch wheels.",
                warnings=[
                    "The app can still run, but generation will be much slower without GPU acceleration.",
                    "Update the NVIDIA driver or override COMFYUI_TORCH_CUDA_INDEX_URL if a compatible PyTorch wheel exists.",
                ],
            )

        return TorchInstallPlan(
            accelerator="nvidia_cuda",
            packages=TORCH_PACKAGES,
            index_url=selected_cuda_index_url,
            pip_args=["--index-url", selected_cuda_index_url],
            reason="NVIDIA GPU detected; installing CUDA-enabled PyTorch wheels.",
            warnings=[
                "CUDA wheel selection is policy-driven and can be updated as PyTorch support changes.",
                "The app still requires a compatible NVIDIA driver on the host machine.",
            ],
        )

    if hardware.os_name in {"Linux", "Windows"}:
        return TorchInstallPlan(
            accelerator="cpu",
            packages=TORCH_PACKAGES,
            index_url=cpu_index_url,
            pip_args=["--index-url", cpu_index_url],
            reason="No supported GPU backend detected; installing CPU-only PyTorch wheels.",
        )

    if hardware.accelerator == "apple_mps":
        return TorchInstallPlan(
            accelerator="apple_mps",
            packages=TORCH_PACKAGES,
            reason="Apple Silicon macOS detected; installing PyTorch macOS wheels with MPS support when available.",
        )

    return TorchInstallPlan(
        accelerator=UNSUPPORTED_MACOS_INTEL_ACCELERATOR,
        packages=[],
        reason=(
            "macOS Intel is unsupported for Noofy managed ComfyUI runtime. "
            "No PyTorch or ComfyUI runtime will be installed."
        ),
        warnings=[
            "Noofy supports macOS Apple Silicon, Windows, and Linux managed runtimes.",
        ],
    )


async def _detect_nvidia(
    command_runner: CommandRunner,
    os_name: str,
    machine: str,
    architecture: str,
) -> RuntimeHardwareProfile | None:
    result = await command_runner(
        [
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader",
        ],
        None,
    )
    if result.returncode != 0:
        return None

    gpu_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not gpu_names:
        return None

    summary = await command_runner(["nvidia-smi"], None)
    cuda_version = _parse_cuda_version(summary.stdout)
    notes = ["NVIDIA GPU detected through nvidia-smi."]
    if cuda_version is not None:
        notes.append(f"Detected NVIDIA driver CUDA capability: {cuda_version}.")

    return RuntimeHardwareProfile(
        os_name=os_name,
        os_version=platform.version() or None,
        machine=machine,
        architecture=architecture,
        accelerator="nvidia_cuda",
        gpu_names=gpu_names,
        cuda_version=cuda_version,
        notes=notes,
    )


def _parse_cuda_version(output: str) -> str | None:
    match = re.search(r"CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)", output)
    return match.group(1) if match else None


def _select_cuda_index_url(cuda_version: str | None) -> str | None:
    supported_versions = [
        ((13, 0), "https://download.pytorch.org/whl/cu130"),
        ((12, 8), "https://download.pytorch.org/whl/cu128"),
        ((12, 6), "https://download.pytorch.org/whl/cu126"),
        ((12, 4), "https://download.pytorch.org/whl/cu124"),
        ((12, 1), "https://download.pytorch.org/whl/cu121"),
        ((11, 8), "https://download.pytorch.org/whl/cu118"),
    ]
    if cuda_version is None:
        return supported_versions[0][1]

    parsed = _parse_version(cuda_version)
    if parsed is None:
        return supported_versions[0][1]

    for minimum_version, index_url in supported_versions:
        if parsed >= minimum_version:
            return index_url
    return None


def _parse_version(version: str) -> tuple[int, int] | None:
    match = re.match(r"^([0-9]+)(?:\.([0-9]+))?", version)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0)
