from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import websockets


async def smoke_required_routes(base_url: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        for path in ("/system_stats", "/object_info", "/models", "/queue", "/history"):
            response = await client.get(f"{base_url}{path}")
            if not required_route_status_usable(path, response.status_code):
                raise RuntimeError(
                    f"ComfyUI smoke route failed: {path} -> {response.status_code}"
                )
        view_response = await client.get(
            f"{base_url}/view",
            params={"filename": "__noofy_missing__.png", "type": "output"},
        )
        if not required_route_status_usable("/view", view_response.status_code):
            raise RuntimeError(
                f"ComfyUI smoke route failed: /view -> {view_response.status_code}"
            )


def required_route_status_usable(path: str, status_code: int) -> bool:
    if path == "/view":
        return status_code < 500 and status_code != 405
    return 200 <= status_code < 300


async def smoke_prompt_and_websocket(base_url: str, ws_url: str) -> None:
    client_id = f"noofy-smoke-{uuid4().hex}"
    prompt = {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 16, "height": 16, "batch_size": 1, "color": 0},
        },
        "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
    }
    async with websockets.connect(f"{ws_url}?clientId={client_id}") as websocket:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{base_url}/prompt", json={"prompt": prompt, "client_id": client_id}
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"ComfyUI smoke prompt failed: {response.status_code} {response.text[:200]}"
                )
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(websocket.recv(), timeout=5)
            if isinstance(message, bytes):
                continue
            payload = json.loads(message)
            if payload.get("type") == "executing":
                data = payload.get("data")
                if isinstance(data, dict) and data.get("node") is None:
                    return
        raise RuntimeError(
            "ComfyUI smoke WebSocket did not report workflow completion."
        )


def assert_no_runtime_dirs_in_source(source_dir: Path) -> None:
    forbidden = {
        "models",
        "input",
        "output",
        "temp",
        "custom_nodes",
        "user",
        "__pycache__",
    }
    present = sorted(
        path.name for path in source_dir.iterdir() if path.name in forbidden
    )
    if present:
        raise RuntimeError(
            f"ComfyUI source contains runtime directories after smoke test: {', '.join(present)}"
        )
