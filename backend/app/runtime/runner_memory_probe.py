"""Noofy-owned runner-side memory telemetry wrapper.

This module is executed by the runner Python interpreter and then runs the
target ComfyUI entrypoint in the same process. That lets Noofy sample PyTorch
CUDA/MPS allocator state and Windows DXGI budget state without modifying the
vendored ComfyUI source.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import runpy
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--telemetry-file", required=True)
    parser.add_argument("--sample-window", default="runner_startup")
    parser.add_argument("--sample-interval-seconds", type=float, default=0.5)
    parser.add_argument("target", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    target = list(args.target)
    if target and target[0] == "--":
        target = target[1:]
    if not target:
        raise SystemExit("runner_memory_probe requires a target entrypoint")

    telemetry_path = Path(args.telemetry_file)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    sampler = threading.Thread(
        target=_sample_loop,
        kwargs={
            "telemetry_path": telemetry_path,
            "runner_id": args.runner_id,
            "sample_window": args.sample_window,
            "interval_seconds": max(args.sample_interval_seconds, 0.1),
            "stop_event": stop_event,
        },
        daemon=True,
    )
    sampler.start()
    try:
        _run_target(target)
    finally:
        _write_sample(telemetry_path, args.runner_id, args.sample_window)
        stop_event.set()
        sampler.join(timeout=1)
    return 0


def _run_target(target: list[str]) -> None:
    entrypoint = Path(target[0])
    sys.argv = target
    if entrypoint.parent:
        sys.path.insert(0, str(entrypoint.parent.resolve()))
    runpy.run_path(str(entrypoint), run_name="__main__")


def _sample_loop(
    *,
    telemetry_path: Path,
    runner_id: str,
    sample_window: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        _write_sample(telemetry_path, runner_id, sample_window)
        stop_event.wait(interval_seconds)


def _write_sample(telemetry_path: Path, runner_id: str, sample_window: str) -> None:
    payload = _sample_payload(runner_id, sample_window)
    try:
        with telemetry_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        return


def _sample_payload(runner_id: str, sample_window: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "0.1.0",
        "runner_id": runner_id,
        "pid": os.getpid(),
        "sample_window": sample_window,
        "observed_at": datetime.now(UTC).isoformat(),
        "signal_sources": [],
        "attribution_reasons": [],
    }
    torch_payload = _sample_torch()
    if torch_payload:
        payload.update(torch_payload)
    dxgi = _sample_dxgi_video_memory_info()
    if dxgi:
        payload["dxgi"] = dxgi
        payload["backend"] = payload.get("backend") or "directml"
        payload["signal_sources"].append("dxgi_query_video_memory_info")
        payload["attribution_reasons"].append("runner_side_dxgi_video_memory_info")
    return payload


def _sample_torch() -> dict[str, Any]:
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:
        return {}
    payload: dict[str, Any] = {}
    cuda = _sample_torch_cuda(torch)
    if cuda:
        payload["backend"] = "cuda"
        payload["cuda"] = cuda
        payload.setdefault("signal_sources", []).append("pytorch_cuda_allocator")
        payload.setdefault("attribution_reasons", []).append("runner_side_cuda_allocator_stats")
    mps = _sample_torch_mps(torch)
    if mps:
        payload["backend"] = payload.get("backend") or "mps"
        payload["mps"] = mps
        payload.setdefault("signal_sources", []).append("pytorch_mps_allocator")
        payload.setdefault("attribution_reasons", []).append("runner_side_mps_allocator_stats")
    return payload


def _sample_torch_cuda(torch: Any) -> dict[str, int]:
    cuda = getattr(torch, "cuda", None)
    if cuda is None:
        return {}
    try:
        if not cuda.is_available():
            return {}
    except Exception:
        return {}
    data: dict[str, int] = {}
    for key, attr in {
        "allocated_current_bytes": "memory_allocated",
        "reserved_current_bytes": "memory_reserved",
        "allocated_peak_bytes": "max_memory_allocated",
        "reserved_peak_bytes": "max_memory_reserved",
    }.items():
        try:
            data[key] = int(getattr(cuda, attr)())
        except Exception:
            pass
    try:
        stats = cuda.memory_stats()
    except Exception:
        stats = {}
    if isinstance(stats, dict):
        for source_key, target_key in {
            "num_ooms": "oom_count",
            "num_alloc_retries": "alloc_retry_count",
        }.items():
            value = stats.get(source_key)
            if isinstance(value, int):
                data[target_key] = value
    return data


def _sample_torch_mps(torch: Any) -> dict[str, int]:
    mps = getattr(torch, "mps", None)
    if mps is None:
        return {}
    try:
        backend = getattr(torch.backends, "mps", None)
        if backend is not None and not backend.is_available():
            return {}
    except Exception:
        return {}
    data: dict[str, int] = {}
    for key, attr in {
        "current_allocated_bytes": "current_allocated_memory",
        "driver_allocated_bytes": "driver_allocated_memory",
        "recommended_max_bytes": "recommended_max_memory",
    }.items():
        try:
            data[key] = int(getattr(mps, attr)())
        except Exception:
            pass
    return data


def _sample_dxgi_video_memory_info() -> dict[str, int]:
    if os.name != "nt":
        return {}
    try:
        dxgi = ctypes.windll.dxgi  # type: ignore[attr-defined]
    except Exception:
        return {}
    # This intentionally stays best-effort. If the host's DXGI COM surface cannot
    # be reached through ctypes, Noofy falls back to Windows process counters.
    try:
        create_factory = dxgi.CreateDXGIFactory1
    except AttributeError:
        return {}
    # A complete ctypes COM wrapper is fragile across Python/Windows variants.
    # Real hardware validation should replace or harden this with a tiny native
    # Noofy helper if this best-effort path is not reliable enough.
    del create_factory
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
