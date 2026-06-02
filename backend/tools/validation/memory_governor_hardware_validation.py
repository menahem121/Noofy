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
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / ".noofy-runtime" / "data"
DEFAULT_OUTPUT = REPO_ROOT / ".noofy-runtime" / "validation" / "memory-governor-linux-validation.json"
RUNNER_MEMORY_PROBE = BACKEND_ROOT / "app" / "runtime" / "runners" / "runner_memory_probe.py"
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
    from app.runtime.memory.memory_governor import RunnerMemoryTelemetryReader, MemorySampleWindow

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

        runner = service.runner_supervisor.runner_for_job(job.job_id)
        telemetry_path = runner.memory_telemetry_path
        lifecycle_validation = await _validate_p0_p1_lifecycle(
            service,
            initial_job=job,
            initial_result=result,
            runner_id=runner.runner_id,
        )
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
                "p0_p1_lifecycle": lifecycle_validation,
            }
        )
    except Exception as exc:
        summary["error"] = str(exc)
        summary["logs"] = [event.model_dump(mode="json") for event in service.list_logs(limit=200).events]
    finally:
        await service.shutdown()
        shutdown_processes = _nvidia_compute_process_probe()
        runner_pid = (summary.get("runner") or {}).get("pid")
        runner_pid_present = any(
            process.get("pid") == runner_pid
            for process in shutdown_processes["processes"]
        )
        summary["post_shutdown_cuda_processes"] = {
            **shutdown_processes,
            "validated_runner_pid": runner_pid,
            "runner_pid_present": runner_pid_present,
        }
        if summary["status"] == "passed" and runner_pid_present:
            summary["status"] = "failed"
            summary["error"] = f"Managed runner process survived shutdown: {runner_pid}"

    output = json.dumps(summary, indent=2, sort_keys=True)
    print(output, flush=True)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(output + "\n", encoding="utf-8")
    return 0 if summary["status"] == "passed" else 1


async def _wait_for_result(service: Any, job_id: str):
    deadline = time.time() + 120
    while time.time() < deadline:
        progress = await service.get_progress(job_id)
        if progress.status in {"completed", "failed", "canceled"}:
            return await service.get_result(job_id)
        await asyncio.sleep(0.5)
    return await service.get_result(job_id)


async def _validate_p0_p1_lifecycle(
    service: Any,
    *,
    initial_job: Any,
    initial_result: Any,
    runner_id: str,
) -> dict[str, Any]:
    initial = _completed_case_summary(
        service,
        initial_job,
        initial_result,
        runner_id=runner_id,
    )
    prompt_job, prompt_result = await _run_completed_workflow(
        service,
        inputs={"prompt": "a prompt-only CUDA validation edit"},
    )
    prompt = _completed_case_summary(service, prompt_job, prompt_result, runner_id=runner_id)
    if prompt["input_profile_fingerprint"] != initial["input_profile_fingerprint"]:
        raise RuntimeError("Prompt-only rerun created a new memory profile bucket")
    _require_warm_reuse(prompt, scenario="prompt-only rerun")

    seed_job, seed_result = await _run_completed_workflow(
        service,
        inputs={"seed": 0x445566},
    )
    seed = _completed_case_summary(service, seed_job, seed_result, runner_id=runner_id)
    if seed["input_profile_fingerprint"] != initial["input_profile_fingerprint"]:
        raise RuntimeError("Seed-only rerun created a new memory profile bucket")
    _require_warm_reuse(seed, scenario="seed-only rerun")

    profile_job, profile_result = await _run_completed_workflow(
        service,
        inputs={"width": 128, "height": 96, "batch_size": 2},
    )
    profile = _completed_case_summary(service, profile_job, profile_result, runner_id=runner_id)
    if profile["input_profile_fingerprint"] == initial["input_profile_fingerprint"]:
        raise RuntimeError("Resolution and batch rerun reused the smaller memory profile bucket")
    runtime_features = profile["runtime_estimate_features"]
    if (
        runtime_features.get("resolution_width"),
        runtime_features.get("resolution_height"),
        runtime_features.get("batch_size"),
    ) != (128, 96, 2):
        raise RuntimeError(f"Memory-changing inputs were not extracted into the estimate: {runtime_features}")

    queued_handoff = await _validate_rapid_queued_handoff(service)
    queued_cancellation = await _validate_queued_cancellation(service)
    isolated_release = await _validate_isolated_runner_release(
        service,
        runner_id=runner_id,
        source_job=profile_job,
    )
    return {
        "initial": initial,
        "prompt_only_warm_rerun": prompt,
        "seed_only_warm_rerun": seed,
        "memory_changing_rerun": profile,
        "rapid_double_click_queued_handoff": queued_handoff,
        "queued_cancellation": queued_cancellation,
        "isolated_runner_release_polling": isolated_release,
    }


async def _run_completed_workflow(
    service: Any,
    *,
    inputs: dict[str, Any],
):
    from app.engine.models import EngineJob

    job = await service.run_workflow(WORKFLOW_ID, inputs, {})
    if not isinstance(job, EngineJob):
        raise RuntimeError(f"Workflow rerun did not return an EngineJob: {job}")
    if job.status in {"blocked_by_memory", "queued_pending_memory"}:
        raise RuntimeError(f"Workflow rerun did not start immediately: {job.model_dump(mode='json')}")
    result = await _wait_for_result(service, job.job_id)
    if result.status != "completed":
        raise RuntimeError(f"Workflow rerun did not complete: {result.model_dump(mode='json')}")
    return job, result


def _completed_case_summary(
    service: Any,
    job: Any,
    result: Any,
    *,
    runner_id: str,
) -> dict[str, Any]:
    actual_runner = service.runner_supervisor.runner_for_job(job.job_id)
    if actual_runner.runner_id != runner_id:
        raise RuntimeError(
            f"Workflow rerun moved runners unexpectedly: {actual_runner.runner_id} != {runner_id}"
        )
    memory_decision = job.memory_decision or {}
    return {
        "job_id": job.job_id,
        "runner_id": actual_runner.runner_id,
        "job_status": job.status,
        "result_status": result.status,
        "input_profile_fingerprint": service._job_memory_profile_fingerprints[job.job_id],
        "memory_action": memory_decision.get("action"),
        "memory_state": (job.memory_status or {}).get("state"),
        "memory_ownership": (memory_decision.get("developer_details") or {}).get("memory_ownership"),
        "runtime_estimate_features": (memory_decision.get("developer_details") or {}).get(
            "runtime_estimate_features",
            {},
        ),
    }


def _require_warm_reuse(summary: dict[str, Any], *, scenario: str) -> None:
    if summary["memory_action"] != "reuse_runner":
        raise RuntimeError(f"{scenario} did not reuse the warm runner: {summary}")
    ownership = summary.get("memory_ownership") or {}
    if ownership.get("same_warm_runner_id") != summary["runner_id"]:
        raise RuntimeError(f"{scenario} did not classify warm runner VRAM correctly: {summary}")


async def _validate_rapid_queued_handoff(service: Any) -> dict[str, Any]:
    direct, queued = await _start_rapid_pair(
        service,
        first_inputs={"width": 2048, "height": 2048, "batch_size": 8, "seed": 0x112233},
        second_inputs={"width": 2048, "height": 2048, "batch_size": 8, "seed": 0x223344},
    )
    queue_id = queued.queue_id
    assert queue_id is not None
    record_after_clicks = service.workflow_run_queue_service.get(queue_id)
    direct_result = await _wait_for_result(service, direct.job_id)
    queued_result = await _wait_for_result(service, queue_id)
    resolved = service.workflow_run_queue_service.resolve(queue_id)
    if direct_result.status != "completed" or queued_result.status != "completed":
        raise RuntimeError(
            f"Rapid-click workflows did not complete: direct={direct_result}, queued={queued_result}"
        )
    if resolved.job_id == queue_id or queued_result.job_id != resolved.job_id:
        raise RuntimeError(
            f"Queue alias did not resolve to submitted job: queue_id={queue_id}, resolved={resolved}"
        )
    if queued_result.queue_id != queue_id:
        raise RuntimeError(f"Queued result did not preserve its public queue ID: {queued_result}")
    terminal_record = resolved.record
    if terminal_record is None:
        raise RuntimeError(f"Submitted queue alias lost its retained queue record: {resolved}")
    if terminal_record.attempt_count > 3 or terminal_record.transient_failure_count:
        raise RuntimeError(f"Rapid-click queue handoff was not bounded cleanly: {terminal_record}")
    if terminal_record.reservation_token is not None:
        raise RuntimeError(f"Submitted queue alias retained a stale reservation: {terminal_record}")
    return {
        "direct_job_id": direct.job_id,
        "queued_public_id": queue_id,
        "submitted_job_id": resolved.job_id,
        "initial_queue_record": record_after_clicks.model_dump(mode="json")
        if record_after_clicks is not None
        else None,
        "terminal_queue_record": terminal_record.model_dump(mode="json"),
        "direct_result_status": direct_result.status,
        "queued_result_status": queued_result.status,
        "alias_preserved_on_result": queued_result.queue_id == queue_id,
    }


async def _validate_queued_cancellation(service: Any) -> dict[str, Any]:
    direct, queued = await _start_rapid_pair(
        service,
        first_inputs={"width": 2048, "height": 2048, "batch_size": 8, "seed": 0x334455},
        second_inputs={"width": 2048, "height": 2048, "batch_size": 8, "seed": 0x556677},
    )
    queue_id = queued.queue_id
    assert queue_id is not None
    canceled = await service.cancel_job(queue_id)
    direct_result = await _wait_for_result(service, direct.job_id)
    await asyncio.sleep(0.25)
    record = service.workflow_run_queue_service.get(queue_id)
    if canceled.status != "canceled":
        raise RuntimeError(f"Queued run cancellation did not report canceled: {canceled}")
    if direct_result.status != "completed":
        raise RuntimeError(f"Active workflow did not survive queued cancellation: {direct_result}")
    if (
        record is None
        or record.status.value != "canceled"
        or record.submitted_job_id is not None
        or record.reservation_token is not None
        or record.attempt_count > 2
    ):
        raise RuntimeError(f"Canceled queued workflow was submitted unexpectedly: {record}")
    return {
        "active_job_id": direct.job_id,
        "queued_public_id": queue_id,
        "cancel_status": canceled.status,
        "active_result_status": direct_result.status,
        "terminal_queue_record": record.model_dump(mode="json"),
    }


async def _start_rapid_pair(
    service: Any,
    *,
    first_inputs: dict[str, Any],
    second_inputs: dict[str, Any],
):
    from app.engine.models import EngineJob

    jobs = await asyncio.gather(
        service.run_workflow(WORKFLOW_ID, first_inputs, {}),
        service.run_workflow(WORKFLOW_ID, second_inputs, {}),
    )
    if not all(isinstance(job, EngineJob) for job in jobs):
        raise RuntimeError(f"Rapid workflow submissions did not return EngineJobs: {jobs}")
    direct = [job for job in jobs if job.status != "queued_pending_memory"]
    queued = [job for job in jobs if job.status == "queued_pending_memory"]
    if len(direct) != 1 or len(queued) != 1:
        raise RuntimeError(f"Rapid workflow submissions were not serialized safely: {jobs}")
    return direct[0], queued[0]


async def _validate_isolated_runner_release(
    service: Any,
    *,
    runner_id: str,
    source_job: Any,
) -> dict[str, Any]:
    from app.runtime.memory.memory_governor import (
        MemoryDecisionAction,
        MemoryGovernorDecision,
        MemoryReleaseStatus,
    )
    from app.runtime.runners.supervisor import RunnerStatus

    if source_job.memory_decision is None:
        raise RuntimeError("Cannot exercise isolated runner release without a memory decision")
    before = service.memory_observer.snapshot()
    decision = MemoryGovernorDecision.model_validate(source_job.memory_decision).model_copy(
        update={
            "decision_id": f"mg-hardware-release-{uuid4().hex}",
            "action": MemoryDecisionAction.EVICT_THEN_START,
            "evict_runner_ids": [runner_id],
        }
    )
    cleaned_up = await service.memory_service.cleanup_idle_runners_for_memory_decision(
        decision,
        metric_name="hardware_validation_idle_runner_evicted",
        log_source="memory_governor",
        log_message="Released idle runner during CUDA hardware validation",
        runner_ids=[runner_id],
    )
    release = await service.memory_service.wait_for_memory_release_after_cleanup(decision)
    after = service.memory_observer.snapshot()
    descriptor = service.runner_supervisor.get_runner(runner_id)
    if not cleaned_up or release.status is not MemoryReleaseStatus.RELEASED:
        raise RuntimeError(f"Isolated runner release polling did not confirm release: {release}")
    if descriptor.status is not RunnerStatus.STOPPED:
        raise RuntimeError(f"Isolated runner did not finish stopped after release: {descriptor}")
    return {
        "cleanup_requested": cleaned_up,
        "release_status": release.status.value,
        "release_reason_code": release.reason_code,
        "timeline": release.timeline,
        "runner_status_after_release": descriptor.status.value,
        "free_vram_mb_before": before.free_vram_mb,
        "free_vram_mb_after": after.free_vram_mb,
    }


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


def _nvidia_compute_process_probe() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "error": str(exc), "processes": []}
    processes = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", maxsplit=2)]
        if len(fields) != 3:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        processes.append(
            {
                "pid": pid,
                "process_name": fields[1],
                "used_memory_mb": fields[2],
            }
        )
    return {
        "available": result.returncode == 0,
        "error": result.stderr.strip() or None,
        "processes": processes,
    }


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
    result = subprocess.run(
        [
            python_executable,
            str(RUNNER_MEMORY_PROBE),
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
    from app.runtime.fingerprints import (
        FINGERPRINT_SCHEMA_VERSION,
        dependency_env_fingerprint,
        runner_workspace_fingerprint,
        sha256_fingerprint,
    )
    from app.runtime.profiles import (
        DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
        load_runtime_profile_catalog,
        resolve_runtime_profile,
    )

    catalog = load_runtime_profile_catalog(DEFAULT_RUNTIME_PROFILE_CATALOG_PATH)
    selection = resolve_runtime_profile(
        catalog,
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id="linux-x64-cuda130",
    )
    profile = selection.profile
    variant = selection.variant
    dependency_fingerprint = dependency_env_fingerprint(
        runtime_profile_id=profile.runtime_profile_id,
        runtime_profile_manifest_hash=profile.runtime_profile_manifest_hash,
        runtime_profile_variant_id=variant.runtime_profile_variant_id,
        os_name=variant.os,
        architecture=variant.architecture,
        python_build_id=variant.python_build_id,
        torch_wheel_build_tag=variant.torch_wheel_build_tag,
        torch_backend=variant.gpu_backend_profile,
        dependency_lock_hash=variant.core_dependency_lock_hash,
        install_policy_version="core_only_no_community",
    )
    runner_fingerprint = runner_workspace_fingerprint(
        dependency_env_fingerprint=dependency_fingerprint,
        runtime_profile_id=profile.runtime_profile_id,
        runtime_profile_manifest_hash=profile.runtime_profile_manifest_hash,
        runtime_profile_variant_id=variant.runtime_profile_variant_id,
        comfyui_source_hash=profile.comfyui_core_source_hash,
        comfyui_frontend_version=profile.comfyui_frontend_version,
        enabled_custom_node_manifest_hash=sha256_fingerprint([]),
        launch_config_hash=sha256_fingerprint({"validation_workflow_id": WORKFLOW_ID}),
        model_view_hash=sha256_fingerprint([]),
    )
    validation_capsule_fingerprint = sha256_fingerprint(
        {
            "kind": "memory_governor_validation_capsule",
            "runner_fingerprint": runner_fingerprint,
        }
    )
    workflow_dir = data_dir / "workflows" / WORKFLOW_ID
    workflow_dir.mkdir(parents=True, exist_ok=True)
    prompt = _empty_image_prompt(include_profile_inputs=True)
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
        "inputs": [
            {
                "id": "prompt",
                "label": "Prompt",
                "control": "textarea",
                "binding": {"node_id": "3", "input_name": "text"},
                "default": "CUDA validation prompt",
            },
            {
                "id": "seed",
                "label": "Seed",
                "control": "seed_widget",
                "binding": {"node_id": "1", "input_name": "color"},
                "default": 0x335577,
            },
            {
                "id": "width",
                "label": "Width",
                "control": "int_field",
                "binding": {"node_id": "1", "input_name": "width"},
                "default": 64,
            },
            {
                "id": "height",
                "label": "Height",
                "control": "int_field",
                "binding": {"node_id": "1", "input_name": "height"},
                "default": 64,
            },
            {
                "id": "batch_size",
                "label": "Batch size",
                "control": "int_field",
                "binding": {"node_id": "1", "input_name": "batch_size"},
                "default": 1,
            },
        ],
        "outputs": [
            {
                "id": "image",
                "label": "Image",
                "node_id": "2",
                "type": "image",
            }
        ],
        "dashboard": {
            "version": "0.1.0",
            "status": "configured",
            "sections": [
                {
                    "id": "main",
                    "title": "Main",
                    "controls": [
                        {
                            "id": "prompt",
                            "type": "textarea",
                            "label": "Prompt",
                            "input_id": "prompt",
                        },
                        {
                            "id": "seed",
                            "type": "seed_widget",
                            "label": "Seed",
                            "input_id": "seed",
                        },
                        {
                            "id": "width",
                            "type": "int_field",
                            "label": "Width",
                            "input_id": "width",
                        },
                        {
                            "id": "height",
                            "type": "int_field",
                            "label": "Height",
                            "input_id": "height",
                        },
                        {
                            "id": "batch_size",
                            "type": "int_field",
                            "label": "Batch size",
                            "input_id": "batch_size",
                        },
                        {
                            "id": "image",
                            "type": "display_image",
                            "label": "Image",
                            "output_id": "image",
                        }
                    ],
                }
            ],
        },
        "smoke_tests": {
            "workflow_execution": {
                "name": "memory-governor-empty-image",
                "prompt": _empty_image_prompt(),
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
            "comfyui_version": profile.comfyui_core_version,
            "core_source_hash": profile.comfyui_core_source_hash,
        },
        "runtime": {
            "runtime_profile_id": profile.runtime_profile_id,
            "runtime_profile_variant_id": variant.runtime_profile_variant_id,
            "runtime_profile_manifest_hash": profile.runtime_profile_manifest_hash,
            "runtime_profile_catalog_version": catalog.schema_version,
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            "dependency_env_fingerprint": dependency_fingerprint,
            "runner_fingerprint": runner_fingerprint,
            "runner_process_compatibility_key": "memory-governor-empty-image-validation",
            "capsule_fingerprint": validation_capsule_fingerprint,
            "os": variant.os,
            "architecture": variant.architecture,
            "python_version": variant.python_version,
            "python_build_id": variant.python_build_id,
            "gpu_backend": variant.gpu_backend_profile,
            "dependency_lock_hash": variant.core_dependency_lock_hash,
            "runner_workspace_hash": runner_fingerprint,
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


def _empty_image_prompt(*, include_profile_inputs: bool = False) -> dict[str, object]:
    prompt: dict[str, object] = {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0x335577},
        },
        "2": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "noofy_memory_governor_validation"},
        },
    }
    if include_profile_inputs:
        # Disconnected prompt metadata lets the hardware pass exercise prompt
        # fingerprinting without requiring a multi-gigabyte diffusion model.
        prompt["3"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "CUDA validation prompt", "clip": ["unused-validation-clip", 0]},
        }
    return prompt


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
