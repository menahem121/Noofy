"""Persistent store for mutable workflow install state.

Capsule locks are immutable resolved facts. Install state, by contrast,
records what has actually happened on this machine: whether the capsule's
models are downloaded, whether smoke tests passed, when it was last used.
The two records must stay separate so corrupt install state never poisons
the immutable lock.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.runtime.isolation import (
    InstallState,
    InstallStatus,
    InstalledModelReference,
    SmokeTestStatus,
    SmokeTestReport,
)

INSTALL_STATE_SCHEMA_VERSION = "0.1.0"

_UNSET: object = object()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def user_facing_install_message(status: InstallStatus) -> str:
    """Map an install status to the beginner-friendly string the UI shows."""
    return {
        InstallStatus.PENDING: "Not started",
        InstallStatus.IMPORTED: "Imported",
        InstallStatus.NEEDS_INPUT_SETUP: "Needs input setup",
        InstallStatus.PREPARING: "Preparing workflow",
        InstallStatus.RESOLVING_RUNTIME_PROFILE: "Checking runtime support",
        InstallStatus.RESOLVING_MODELS: "Checking required models",
        InstallStatus.RESOLVING_DEPENDENCIES: "Preparing workflow support files",
        InstallStatus.MATERIALIZING_CUSTOM_NODES: "Preparing workflow extensions",
        InstallStatus.MATERIALIZING_MODEL_VIEW: "Preparing model access",
        InstallStatus.DOWNLOADING: "Downloading required models",
        InstallStatus.CHECKING_COMPATIBILITY: "Checking compatibility",
        InstallStatus.SMOKE_TESTING: "Testing workflow startup",
        InstallStatus.READY: "Ready",
        InstallStatus.PREPARED_NEEDS_INPUT_SETUP: "Needs input setup",
        InstallStatus.CANNOT_PREPARE_AUTOMATICALLY: "Cannot prepare automatically",
        InstallStatus.UNSUPPORTED_RUNTIME_PROFILE: "Unsupported runtime",
        InstallStatus.BLOCKED_BY_POLICY: "Blocked by safety policy",
        InstallStatus.FAILED: "Cannot prepare automatically",
        InstallStatus.UNSUPPORTED: "Unsupported",
    }[status]


class InstallStateStore:
    """File-backed store keyed by capsule fingerprint."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self._lock = threading.Lock()

    def get(self, capsule_fingerprint: str) -> InstallState | None:
        path = self._path_for(capsule_fingerprint)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return InstallState.model_validate(data)

    def get_or_create(self, capsule_fingerprint: str) -> InstallState:
        existing = self.get(capsule_fingerprint)
        if existing is not None:
            return existing
        new_state = InstallState(
            schema_version=INSTALL_STATE_SCHEMA_VERSION,
            capsule_fingerprint=capsule_fingerprint,
            status=InstallStatus.PENDING,
            smoke_test_status=SmokeTestStatus.NOT_RUN,
        )
        self.save(new_state)
        return new_state

    def save(self, state: InstallState) -> InstallState:
        """Atomically persist `state` to disk."""
        path = self._path_for(state.capsule_fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = state.model_dump_json()
        with self._lock:
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(path)
        return state

    def update(
        self,
        capsule_fingerprint: str,
        *,
        status: InstallStatus | None = None,
        last_error: str | None | object = _UNSET,
        runtime_profile_variant_id: str | None | object = _UNSET,
        runtime_profile_manifest_hash: str | None | object = _UNSET,
        runtime_profile_catalog_version: str | None | object = _UNSET,
        dependency_env_fingerprint: str | None | object = _UNSET,
        runner_workspace_fingerprint: str | None | object = _UNSET,
        runner_process_compatibility_key: str | None | object = _UNSET,
        dependency_env_path: str | None | object = _UNSET,
        runner_workspace_path: str | None | object = _UNSET,
        model_references: list[InstalledModelReference] | object = _UNSET,
        smoke_test_status: SmokeTestStatus | None = None,
        smoke_test_report: SmokeTestReport | object = _UNSET,
        installed_at: str | None | object = _UNSET,
        last_used_at: str | None | object = _UNSET,
    ) -> InstallState:
        """Mutate fields on the stored record.

        `_UNSET` lets callers explicitly clear an optional field by passing
        `None` while still distinguishing "leave alone" from "set to None".
        """
        state = self.get_or_create(capsule_fingerprint)
        updates: dict[str, object] = {}
        if status is not None:
            updates["status"] = status
        if smoke_test_status is not None:
            updates["smoke_test_status"] = smoke_test_status
        if smoke_test_report is not _UNSET:
            updates["smoke_test_report"] = smoke_test_report
        if last_error is not _UNSET:
            updates["last_error"] = last_error
        if runtime_profile_variant_id is not _UNSET:
            updates["runtime_profile_variant_id"] = runtime_profile_variant_id
        if runtime_profile_manifest_hash is not _UNSET:
            updates["runtime_profile_manifest_hash"] = runtime_profile_manifest_hash
        if runtime_profile_catalog_version is not _UNSET:
            updates["runtime_profile_catalog_version"] = runtime_profile_catalog_version
        if dependency_env_fingerprint is not _UNSET:
            updates["dependency_env_fingerprint"] = dependency_env_fingerprint
        if runner_workspace_fingerprint is not _UNSET:
            updates["runner_workspace_fingerprint"] = runner_workspace_fingerprint
        if runner_process_compatibility_key is not _UNSET:
            updates["runner_process_compatibility_key"] = runner_process_compatibility_key
        if dependency_env_path is not _UNSET:
            updates["dependency_env_path"] = dependency_env_path
        if runner_workspace_path is not _UNSET:
            updates["runner_workspace_path"] = runner_workspace_path
        if model_references is not _UNSET:
            updates["model_references"] = model_references
        if installed_at is not _UNSET:
            updates["installed_at"] = installed_at
        if last_used_at is not _UNSET:
            updates["last_used_at"] = last_used_at
        new_state = state.model_copy(update=updates)
        return self.save(new_state)

    def list_states(self) -> list[InstallState]:
        if not self.root_dir.exists():
            return []
        states: list[InstallState] = []
        for path in sorted(self.root_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            states.append(InstallState.model_validate(data))
        return states

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path_for(self, capsule_fingerprint: str) -> Path:
        safe = capsule_fingerprint.replace("/", "_").replace(":", "_")
        return self.root_dir / f"{safe}.json"
