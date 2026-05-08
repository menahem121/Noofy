import argparse
import os
import socket
from copy import deepcopy
from pathlib import Path

import uvicorn
from uvicorn.config import LOGGING_CONFIG


def select_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def runtime_config_line(host: str, port: int) -> str:
    return f"NOOFY_BACKEND_API_BASE_URL=http://{host}:{port}/api"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Noofy backend API.")
    parser.add_argument("--host", default=os.environ.get("NOOFY_BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NOOFY_BACKEND_PORT", "0")))
    parser.add_argument("--api-base-url-file", type=Path, default=None)
    parser.add_argument("--log-level", default=os.environ.get("NOOFY_BACKEND_LOG_LEVEL", "info"))
    return parser.parse_args()


def uvicorn_log_config() -> dict[str, object]:
    config = deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["noofy_resource_monitor"] = {
        "()": "app.core.logging.SuppressResourceMonitorAccessLogFilter",
    }
    config["handlers"]["access"]["filters"] = ["noofy_resource_monitor"]
    return config


def main() -> None:
    args = parse_args()
    port = args.port or select_free_port(args.host)
    api_base_url = f"http://{args.host}:{port}/api"

    if args.api_base_url_file is not None:
        args.api_base_url_file.parent.mkdir(parents=True, exist_ok=True)
        args.api_base_url_file.write_text(api_base_url, encoding="utf-8")

    print(runtime_config_line(args.host, port), flush=True)
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=port,
        log_level=args.log_level,
        log_config=uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
