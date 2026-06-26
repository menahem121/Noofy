import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.core.paths import NoofyPaths, resolve_paths

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def _csv_env(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    noofy_api_token: str | None = os.environ.get("NOOFY_API_TOKEN")
    noofy_cors_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(os.environ.get("NOOFY_CORS_ORIGINS")) or DEFAULT_CORS_ORIGINS
    )
    comfyui_runtime_mode: str = os.environ.get("COMFYUI_RUNTIME_MODE", "external")
    comfyui_base_url: str = os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    comfyui_ws_url: str | None = os.environ.get("COMFYUI_WS_URL")
    comfyui_python_executable: str | None = os.environ.get("COMFYUI_PYTHON_EXECUTABLE")
    comfyui_bootstrap_python_executable: str = os.environ.get("COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE", "python3")
    comfyui_managed_host: str = os.environ.get("COMFYUI_MANAGED_HOST", "127.0.0.1")
    comfyui_managed_port: int | None = (
        int(os.environ["COMFYUI_MANAGED_PORT"]) if os.environ.get("COMFYUI_MANAGED_PORT") else None
    )
    comfyui_startup_timeout_seconds: float = float(os.environ.get("COMFYUI_STARTUP_TIMEOUT_SECONDS", "60"))
    comfyui_health_poll_interval_seconds: float = float(os.environ.get("COMFYUI_HEALTH_POLL_INTERVAL_SECONDS", "0.5"))
    comfyui_torch_cuda_index_url: str | None = os.environ.get("COMFYUI_TORCH_CUDA_INDEX_URL")
    comfyui_torch_cpu_index_url: str = os.environ.get(
        "COMFYUI_TORCH_CPU_INDEX_URL",
        "https://download.pytorch.org/whl/cpu",
    )
    noofy_bundled_resource_dir: str | None = os.environ.get("NOOFY_BUNDLED_RESOURCE_DIR")
    noofy_runtime_update_repo: str | None = os.environ.get("NOOFY_RUNTIME_UPDATE_REPO")
    comfyui_max_restart_attempts: int = int(os.environ.get("COMFYUI_MAX_RESTART_ATTEMPTS", "3"))
    # Max model files downloaded in parallel. Default serially because provider
    # APIs and multi-GB file hosts are more likely to throttle parallel imports.
    model_download_max_concurrency: int = int(
        os.environ.get("MODEL_DOWNLOAD_MAX_CONCURRENCY", "1")
    )
    # Max models hashed in parallel during verification. Set to 1 to force fully serial
    # verification (e.g. on slow/network/removable model storage).
    model_verification_max_concurrency: int = int(
        os.environ.get("MODEL_VERIFICATION_MAX_CONCURRENCY", "3")
    )
    comfyui_restart_backoff_base: float = float(os.environ.get("COMFYUI_RESTART_BACKOFF_BASE", "2.0"))
    memory_release_timeout_seconds: float = float(
        os.environ.get("NOOFY_MEMORY_RELEASE_TIMEOUT_SECONDS", "8")
    )
    memory_release_initial_poll_interval_seconds: float = float(
        os.environ.get("NOOFY_MEMORY_RELEASE_INITIAL_POLL_INTERVAL_SECONDS", "0.1")
    )
    memory_release_max_poll_interval_seconds: float = float(
        os.environ.get("NOOFY_MEMORY_RELEASE_MAX_POLL_INTERVAL_SECONDS", "1.0")
    )
    # Grace period after the last open workflow view closes before an idle
    # isolated runner becomes eligible for automatic release.
    closed_view_cooldown_seconds: float = float(
        os.environ.get("NOOFY_CLOSED_VIEW_COOLDOWN_SECONDS", "90")
    )
    closed_view_auto_release_enabled: bool = (
        os.environ.get("NOOFY_CLOSED_VIEW_AUTO_RELEASE_ENABLED", "1").strip().lower()
        not in {"0", "false", "no", "off"}
    )
    workflow_lease_ttl_seconds: float = float(
        os.environ.get("NOOFY_WORKFLOW_LEASE_TTL_SECONDS", "120")
    )
    workflow_lease_sweep_interval_seconds: float = float(
        os.environ.get("NOOFY_WORKFLOW_LEASE_SWEEP_INTERVAL_SECONDS", "20")
    )
    noofy_trust_keys_file: str | None = os.environ.get("NOOFY_TRUST_KEYS_FILE")
    comfyui_repo_dir_override_active: bool = bool(os.environ.get("COMFYUI_REPO_DIR"))
    comfyui_python_executable_override_active: bool = bool(os.environ.get("COMFYUI_PYTHON_EXECUTABLE"))
    noofy_packaged_runtime_dir_override_active: bool = bool(os.environ.get("NOOFY_PACKAGED_RUNTIME_DIR"))
    noofy_backend_override_active: bool = bool(
        os.environ.get("NOOFY_BACKEND_DIR")
        or os.environ.get("NOOFY_BACKEND_PYTHON")
        or os.environ.get("NOOFY_BACKEND_SIDECAR")
        or os.environ.get("NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES")
        or os.environ.get("NOOFY_FORCE_PACKAGED_BACKEND")
    )

    @property
    def packaged_runtime_active(self) -> bool:
        return bool(self.noofy_bundled_resource_dir)

    @property
    def developer_runtime_override_active(self) -> bool:
        return (
            self.comfyui_repo_dir_override_active
            or self.comfyui_python_executable_override_active
            or self.noofy_packaged_runtime_dir_override_active
            or self.noofy_backend_override_active
        )

    # Resolved app-owned directory contract.
    paths: NoofyPaths = field(default_factory=resolve_paths)

    # --- Convenience accessors (backward-compatible) ---

    @property
    def runtime_dir(self) -> Path:
        return self.paths.runtime_dir

    @property
    def workflows_dir(self) -> Path:
        """Bundled (read-only) starter workflows."""
        return self.paths.bundled_workflows_dir

    @property
    def comfyui_repo_dir(self) -> Path:
        return self.paths.comfyui_repo_dir

    @property
    def comfyui_models_dir(self) -> Path:
        """App-owned models directory (not the vendored ComfyUI source tree)."""
        return self.paths.models_dir

    @property
    def comfyui_host(self) -> str:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.hostname or "127.0.0.1"

    @property
    def comfyui_port(self) -> int:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.port or 8188

    @property
    def trust_keys_file(self) -> Path:
        return Path(self.noofy_trust_keys_file) if self.noofy_trust_keys_file else self.paths.trust_keys_file


settings = Settings()
