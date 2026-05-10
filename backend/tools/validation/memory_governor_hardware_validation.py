from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / ".noofy-runtime" / "data"
DEFAULT_OUTPUT = REPO_ROOT / ".noofy-runtime" / "validation" / "memory-governor-linux-validation.json"
WORKFLOW_ID = "memory_governor_empty_image_validation"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a managed ComfyUI runtime and validate Linux Memory Governor hardware signals."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--startup-timeout", type=float, default=240)
    parser.add_argument("--skip-bootstrap", action="store_true")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    os.environ["NOOFY_DATA_DIR"] = str(data_dir)
    os.environ["COMFYUI_RUNTIME_MODE"] = "managed"
    os.environ["COMFYUI_STARTUP_TIMEOUT_SECONDS"] = str(args.startup_timeout)
    _prepend_path(Path(sys.executable).parent)

    _write_validation_workflow(data_dir)

    from app.engine.models import EngineJob
    from app.engine.service import create_default_engine_service
    from app.runtime.memory_governor import RunnerMemoryTelemetryReader, MemorySampleWindow

    service = create_default_engine_service()
    summary: dict[str, Any] = {
        "schema_version": "0.1.0",
        "status": "failed",
        "data_dir": str(data_dir),
        "workflow_id": WORKFLOW_ID,
        "backend_python": sys.executable,
        "uv_executable": _which("uv"),
    }
    try:
        if args.skip_bootstrap:
            bootstrap_payload = {"status": "skipped"}
        else:
            bootstrap = await service.bootstrap_comfyui_runtime()
            bootstrap_payload = {
                "status": bootstrap.status,
                "prepared": bootstrap.environment.prepared if bootstrap.environment else None,
                "error": bootstrap.environment.error if bootstrap.environment else None,
                "python_executable": bootstrap.environment.python_executable if bootstrap.environment else None,
                "torch_install_plan": bootstrap.environment.torch_install_plan.model_dump(mode="json")
                if bootstrap.environment and bootstrap.environment.torch_install_plan
                else None,
            }
        managed_python = service.runtime_manager.environment.python_executable
        torch_probe = _managed_torch_probe(managed_python)
        allocator_probe = _allocator_probe(managed_python, data_dir)
        allocator_sample = RunnerMemoryTelemetryReader().sample(
            allocator_probe["telemetry_path"],
            runner_id="memory-governor-allocator-probe",
            sample_window=MemorySampleWindow.WORKFLOW_EXECUTION,
        )

        prepared = await service.prepare_workflow(WORKFLOW_ID)
        started = await service.start_workflow_runner(WORKFLOW_ID)
        if started.get("status") not in {"ready", "idle", "idle_warm"}:
            raise RuntimeError(f"Managed workflow runner did not start: {started}")

        job = await service.run_workflow(WORKFLOW_ID, {}, {})
        if not isinstance(job, EngineJob):
            raise RuntimeError(f"Workflow did not return an EngineJob: {job}")
        if job.status in {"blocked_by_memory", "queued_pending_memory"}:
            raise RuntimeError(f"Workflow did not start immediately: {job.model_dump(mode='json')}")

        result = await _wait_for_result(service, job.job_id)
        if result.status != "completed":
            raise RuntimeError(f"Workflow did not complete: {result.model_dump(mode='json')}")

        memory_events = [
            event.model_dump(mode="json")
            for event in service.list_logs(limit=500).events
            if event.source == "memory_governor"
        ]
        finish_event = next(
            (event for event in memory_events if event["message"] == "Finished best-effort job memory sampling"),
            None,
        )
        local_summaries = (
            [item.model_dump(mode="json") for item in service.memory_learning_store.list_summaries()]
            if service.memory_learning_store is not None
            else []
        )
        runner = service.runner_supervisor.runner_for_job(job.job_id)
        telemetry_path = runner.memory_telemetry_path

        summary.update(
            {
                "status": "passed",
                "bootstrap": bootstrap_payload,
                "managed_torch": torch_probe,
                "allocator_probe": {
                    **allocator_probe,
                    "sample": allocator_sample.model_dump(mode="json"),
                },
                "prepare": _pick(prepared, ["status", "smoke_test_status", "last_error"]),
                "runner": {
                    "runner_id": runner.runner_id,
                    "pid": runner.pid,
                    "status": runner.status.value,
                    "memory_telemetry_path": telemetry_path,
                },
                "job": job.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "memory_governor_metrics": service.memory_governor_metrics(),
                "memory_sampling_finish": finish_event["details"] if finish_event else None,
                "memory_event_count": len(memory_events),
                "memory_events_have_private_paths": "/home/" in json.dumps(memory_events),
                "local_memory_summaries": local_summaries,
            }
        )
    except Exception as exc:
        summary["error"] = str(exc)
        summary["logs"] = [event.model_dump(mode="json") for event in service.list_logs(limit=200).events]
    finally:
        await service.shutdown()

    output = json.dumps(summary, indent=2, sort_keys=True)
    print(output, flush=True)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(output + "\n", encoding="utf-8")
    return 0 if summary["status"] == "passed" else 1


async def _wait_for_result(service: Any, job_id: str):
    deadline = time.time() + 90
    while time.time() < deadline:
        progress = await service.get_progress(job_id)
        if progress.status in {"completed", "failed", "canceled"}:
            return await service.get_result(job_id)
        await asyncio.sleep(0.5)
    return await service.get_result(job_id)


def _managed_torch_probe(python_executable: str) -> dict[str, Any]:
    code = """
import json, sys, torch
print(json.dumps({
  "python": sys.executable,
  "torch_version": torch.__version__,
  "torch_cuda_version": torch.version.cuda,
  "cuda_available": torch.cuda.is_available(),
  "device_count": torch.cuda.device_count(),
  "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
}, sort_keys=True))
"""
    result = subprocess.run(
        [python_executable, "-c", code],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    payload = _json_stdout(result.stdout)
    payload.update({"returncode": result.returncode, "stderr": result.stderr.strip()})
    return payload


def _allocator_probe(python_executable: str, data_dir: Path) -> dict[str, Any]:
    work_dir = data_dir / "validation" / "memory-governor"
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "cuda_allocator_target.py"
    telemetry = work_dir / "allocator-telemetry.jsonl"
    telemetry.unlink(missing_ok=True)
    target.write_text(
        """
import time
import torch

device = torch.device("cuda")
_ = torch.empty((256, 1024, 1024), dtype=torch.uint8, device=device)
torch.cuda.synchronize()
time.sleep(1.0)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    probe = BACKEND_ROOT / "app" / "runtime" / "runner_memory_probe.py"
    result = subprocess.run(
        [
            python_executable,
            str(probe),
            "--runner-id",
            "memory-governor-allocator-probe",
            "--telemetry-file",
            str(telemetry),
            "--sample-window",
            "workflow_execution",
            "--sample-interval-seconds",
            "0.2",
            "--",
            str(target),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    line_count = len(telemetry.read_text(encoding="utf-8").splitlines()) if telemetry.exists() else 0
    return {
        "returncode": result.returncode,
        "stderr": result.stderr.strip(),
        "stdout": result.stdout.strip(),
        "telemetry_path": str(telemetry),
        "telemetry_line_count": line_count,
    }


def _write_validation_workflow(data_dir: Path) -> None:
    workflow_dir = data_dir / "workflows" / WORKFLOW_ID
    workflow_dir.mkdir(parents=True, exist_ok=True)
    prompt = _empty_image_prompt()
    package = {
        "metadata": {
            "id": WORKFLOW_ID,
            "name": "Memory Governor Empty Image Validation",
            "version": "0.1.0",
            "description": "Local hardware validation workflow for the managed ComfyUI runner.",
            "author": "Noofy",
        },
        "engine": "comfyui",
        "required_models": [],
        "comfyui_graph": prompt,
        "smoke_tests": {
            "workflow_execution": {
                "name": "memory-governor-empty-image",
                "prompt": prompt,
                "required_node_types": ["EmptyImage", "SaveImage"],
                "expected_output_node_count": 1,
                "expected_output_node_ids": ["2"],
                "timeout_seconds": 30,
            }
        },
    }
    capsule = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": WORKFLOW_ID,
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "local_validation",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "v0.20.1",
            "core_source_hash": "sha256:cac6d4de1b9111dcc36183321aa4599f49718c63e1918a4ef275dc2cd3656f29",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "linux-x64-cuda130",
            "runtime_profile_manifest_hash": "sha256:e1907e68d12356fdca556b09eae41808f2c508deb44e14aa9a2d65364e559aa4",
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": _fp("memory-governor-dependency-env"),
            "runner_fingerprint": _fp("memory-governor-runner"),
            "runner_process_compatibility_key": "memory-governor-empty-image-validation",
            "capsule_fingerprint": _fp("memory-governor-capsule"),
            "os": "linux",
            "architecture": "x64",
            "python_version": "3.14",
            "python_build_id": "cpython-3.14-noofy-validation",
            "gpu_backend": "cuda",
            "dependency_lock_hash": "sha256:1b727aad6ad34a1cbcd126ff17f9fda0a9b6f22bf22ea8bb0c118987164bce55",
            "runner_workspace_hash": _fp("memory-governor-runner"),
        },
        "custom_nodes": [],
        "dependencies": {
            "lock_file": "core_only_no_community",
            "install_policy": "core_only_no_community",
        },
        "models": [],
        "hardware_observations": {
            "backend": "cuda",
            "observed_peak_vram_mb": 512,
            "observed_peak_ram_mb": 2048,
        },
        "trust": {
            "level": "noofy_verified",
            "publisher": "Noofy",
        },
    }
    (workflow_dir / "package.json").write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (workflow_dir / "capsule.lock.json").write_text(
        json.dumps(capsule, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _empty_image_prompt() -> dict[str, object]:
    return {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0x335577},
        },
        "2": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "noofy_memory_governor_validation"},
        },
    }


def _fp(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _json_stdout(stdout: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return {"stdout": stdout.strip()}
    return parsed if isinstance(parsed, dict) else {"stdout": stdout.strip()}


def _pick(payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys}


def _prepend_path(path: Path) -> None:
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = str(path) + (os.pathsep + current if current else "")


def _which(executable: str) -> str | None:
    import shutil

    return shutil.which(executable)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
