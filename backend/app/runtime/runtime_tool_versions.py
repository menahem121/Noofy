from __future__ import annotations

import json
from pathlib import Path

_VERSIONS_PATH = Path(__file__).with_name("runtime_tool_versions.json")
_VERSIONS = json.loads(_VERSIONS_PATH.read_text(encoding="utf-8"))

SUPPORTED_UV_VERSION = str(_VERSIONS["uv"])
