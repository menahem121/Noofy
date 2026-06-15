"""Transactional runtime install staging and recovery helpers."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.diagnostics import DiagnosticsSink

INSTALL_TRANSACTION_SCHEMA_VERSION = "0.1.0"
INSTALL_TRANSACTION_FILENAME = "transaction.json"
INSTALL_QUARANTINE_FILENAME = "quarantine.json"
DEFAULT_TRANSACTION_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_QUARANTINE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class InstallTransaction:
    transaction_id: str
    root_dir: Path
    workflow_id: str
    capsule_fingerprint: str

    @property
    def dependency_envs_dir(self) -> Path:
        return self.root_dir / "dependency-envs"

    @property
    def runner_workspaces_dir(self) -> Path:
        return self.root_dir / "runner-workspaces"

    @property
    def model_views_dir(self) -> Path:
        return self.root_dir / "model-views"

    @property
    def model_blobs_dir(self) -> Path:
        return self.root_dir / "model-blobs"

    @property
    def smoke_logs_dir(self) -> Path:
        return self.root_dir / "smoke-logs"

    @property
    def manifest_path(self) -> Path:
        return self.root_dir / INSTALL_TRANSACTION_FILENAME

    @property
    def quarantine_path(self) -> Path:
        return self.root_dir / INSTALL_QUARANTINE_FILENAME


@dataclass(frozen=True)
class StartupSweepReport:
    stale_transactions_quarantined: int = 0
    expired_quarantines_removed: int = 0
    stale_tmp_files_removed: int = 0
    stale_lock_files_removed: int = 0
    stale_unscoped_transactions_removed: int = 0


class InstallTransactionStore:
    def __init__(
        self,
        root_dir: Path,
        *,
        log_store: DiagnosticsSink,
        lock_timeout_seconds: float = DEFAULT_TRANSACTION_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.root_dir = root_dir
        self.lock_dir = root_dir / "_locks"
        self.log_store = log_store
        self.lock_timeout_seconds = lock_timeout_seconds
        self._thread_locks: dict[str, threading.Lock] = {}
        self._thread_locks_guard = threading.Lock()

    def open(self, *, workflow_id: str, capsule_fingerprint: str) -> InstallTransaction:
        transaction_id = f"install-{uuid4().hex}"
        transaction = InstallTransaction(
            transaction_id=transaction_id,
            root_dir=self.root_dir / transaction_id,
            workflow_id=workflow_id,
            capsule_fingerprint=capsule_fingerprint,
        )
        for directory in (
            transaction.dependency_envs_dir,
            transaction.runner_workspaces_dir,
            transaction.model_views_dir,
            transaction.model_blobs_dir,
            transaction.smoke_logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=False)
        self._write_json(
            transaction.manifest_path,
            {
                "schema_version": INSTALL_TRANSACTION_SCHEMA_VERSION,
                "transaction_id": transaction.transaction_id,
                "workflow_id": workflow_id,
                "capsule_fingerprint": capsule_fingerprint,
                "status": "preparing",
                "created_at": _now_iso(),
            },
        )
        self.log_store.add(
            "info",
            "Opened runtime install transaction",
            "runtime.install_transaction",
            workflow_id=workflow_id,
            details={
                "transaction_id": transaction.transaction_id,
                "capsule_fingerprint": capsule_fingerprint,
                "path": str(transaction.root_dir),
            },
        )
        return transaction

    def staged_dependency_env_dir(
        self, transaction: InstallTransaction, fingerprint: str
    ) -> Path:
        return (
            transaction.dependency_envs_dir
            / f"dep-env-{_safe_fingerprint(fingerprint)}"
        )

    def staged_runner_workspace_dir(
        self, transaction: InstallTransaction, fingerprint: str
    ) -> Path:
        return (
            transaction.runner_workspaces_dir
            / f"runner-workspace-{_safe_fingerprint(fingerprint)}"
        )

    def write_smoke_report(
        self,
        transaction: InstallTransaction,
        *,
        report: dict[str, object],
        filename: str = "smoke-report.json",
    ) -> Path:
        path = transaction.smoke_logs_dir / filename
        self._write_json(
            path,
            {
                "schema_version": INSTALL_TRANSACTION_SCHEMA_VERSION,
                "transaction_id": transaction.transaction_id,
                "workflow_id": transaction.workflow_id,
                "capsule_fingerprint": transaction.capsule_fingerprint,
                "report": report,
                "written_at": _now_iso(),
            },
        )
        return path

    def diagnostic_logs(self, transaction_id: str) -> dict[str, str]:
        root = self.root_dir / transaction_id
        if not root.is_dir():
            return {}
        logs: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if (
                not path.is_file()
                or path.name in {INSTALL_TRANSACTION_FILENAME, INSTALL_QUARANTINE_FILENAME}
                or path.suffix not in {".log", ".json", ".txt"}
            ):
                continue
            try:
                relative = path.relative_to(root).as_posix()
                logs[relative] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                continue
        return logs

    @contextmanager
    def artifact_lock(self, *, artifact_kind: str, fingerprint: str) -> Iterator[None]:
        lock_key = f"{artifact_kind}-{_safe_fingerprint(fingerprint)}"
        thread_lock = self._thread_lock(lock_key)
        with thread_lock:
            lock_file = self._acquire_file_lock(lock_key)
            try:
                yield
            finally:
                lock_file.unlink(missing_ok=True)

    def mark_promoted(self, transaction: InstallTransaction) -> None:
        self._write_json(
            transaction.manifest_path,
            {
                "schema_version": INSTALL_TRANSACTION_SCHEMA_VERSION,
                "transaction_id": transaction.transaction_id,
                "workflow_id": transaction.workflow_id,
                "capsule_fingerprint": transaction.capsule_fingerprint,
                "status": "promoted",
                "updated_at": _now_iso(),
            },
        )

    def remove(self, transaction: InstallTransaction) -> None:
        shutil.rmtree(transaction.root_dir, ignore_errors=True)

    def quarantine(
        self,
        transaction: InstallTransaction,
        *,
        reason: str,
        retention_days: int = DEFAULT_QUARANTINE_RETENTION_DAYS,
    ) -> None:
        quarantined_at = datetime.now(UTC)
        retain_until = quarantined_at + timedelta(days=retention_days)
        payload = {
            "schema_version": INSTALL_TRANSACTION_SCHEMA_VERSION,
            "transaction_id": transaction.transaction_id,
            "workflow_id": transaction.workflow_id,
            "capsule_fingerprint": transaction.capsule_fingerprint,
            "reason": reason,
            "status": "quarantined",
            "quarantined_at": quarantined_at.isoformat(),
            "retain_until": retain_until.isoformat(),
            "retention_days": retention_days,
        }
        self._write_json(transaction.quarantine_path, payload)
        self._write_json(transaction.manifest_path, payload)
        self.log_store.add(
            "warning",
            "Quarantined runtime install transaction",
            "runtime.install_transaction",
            workflow_id=transaction.workflow_id,
            details={
                "transaction_id": transaction.transaction_id,
                "reason": reason,
                "retain_until": retain_until.isoformat(),
            },
        )

    def sweep_startup(
        self, *, retention_days: int = DEFAULT_QUARANTINE_RETENTION_DAYS
    ) -> StartupSweepReport:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        stale_transactions = 0
        expired_quarantines = 0
        stale_tmp_files = 0
        stale_lock_files = 0
        stale_unscoped_transactions = 0
        now = datetime.now(UTC)
        default_retain_until = now + timedelta(days=retention_days)

        if self.lock_dir.exists():
            for lock_path in self.lock_dir.glob("*.lock"):
                if lock_path.is_file():
                    lock_path.unlink(missing_ok=True)
                    stale_lock_files += 1

        for tmp_path in self.root_dir.rglob("*.tmp"):
            if tmp_path.is_file():
                tmp_path.unlink(missing_ok=True)
                stale_tmp_files += 1

        for path in sorted(self.root_dir.iterdir()):
            if (
                not path.is_dir()
                or path.name == "_locks"
                or path.name.startswith("install-")
            ):
                continue
            if _is_legacy_unscoped_transaction_dir(path):
                shutil.rmtree(path, ignore_errors=True)
                stale_unscoped_transactions += 1

        for path in sorted(self.root_dir.glob("install-*")):
            if not path.is_dir():
                continue
            quarantine_path = path / INSTALL_QUARANTINE_FILENAME
            if quarantine_path.exists():
                retain_until = _read_retain_until(quarantine_path)
                if retain_until is not None and retain_until <= now:
                    shutil.rmtree(path, ignore_errors=True)
                    expired_quarantines += 1
                continue

            transaction = _transaction_from_path(path)
            payload = {
                "schema_version": INSTALL_TRANSACTION_SCHEMA_VERSION,
                "transaction_id": path.name,
                "workflow_id": transaction.get("workflow_id", "unknown"),
                "capsule_fingerprint": transaction.get(
                    "capsule_fingerprint", "unknown"
                ),
                "reason": "Backend stopped before install preparation completed.",
                "status": "quarantined",
                "quarantined_at": now.isoformat(),
                "retain_until": default_retain_until.isoformat(),
                "retention_days": retention_days,
            }
            self._write_json(quarantine_path, payload)
            self._write_json(path / INSTALL_TRANSACTION_FILENAME, payload)
            stale_transactions += 1

        return StartupSweepReport(
            stale_transactions_quarantined=stale_transactions,
            expired_quarantines_removed=expired_quarantines,
            stale_tmp_files_removed=stale_tmp_files,
            stale_lock_files_removed=stale_lock_files,
            stale_unscoped_transactions_removed=stale_unscoped_transactions,
        )

    def _thread_lock(self, lock_key: str) -> threading.Lock:
        with self._thread_locks_guard:
            lock = self._thread_locks.get(lock_key)
            if lock is None:
                lock = threading.Lock()
                self._thread_locks[lock_key] = lock
            return lock

    def _acquire_file_lock(self, lock_key: str) -> Path:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_dir / f"{lock_key}.lock"
        deadline = time.monotonic() + self.lock_timeout_seconds
        while True:
            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(str(os.getpid()))
                return lock_file
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for runtime artifact lock: {lock_key}"
                    )
                time.sleep(0.05)

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tmp_path.replace(path)


def _transaction_from_path(path: Path) -> dict[str, object]:
    manifest = path / INSTALL_TRANSACTION_FILENAME
    if not manifest.exists():
        return {}
    try:
        with manifest.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _is_legacy_unscoped_transaction_dir(path: Path) -> bool:
    return any(
        path.name.startswith(prefix)
        for prefix in (
            "model-",
            "dep-env-",
            "dep-resolve-",
            "runner-workspace-",
            "model-view-",
            "core-engine-",
        )
    )


def _read_retain_until(path: Path) -> datetime | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        value = data.get("retain_until")
        if not isinstance(value, str):
            return None
        return datetime.fromisoformat(value)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_fingerprint(fingerprint: str) -> str:
    return (
        fingerprint.replace("sha256:", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )
