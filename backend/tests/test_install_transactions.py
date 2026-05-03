import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.diagnostics import LogStore
from app.runtime.install_transactions import InstallTransactionStore


def test_startup_sweep_quarantines_stale_install_transaction_idempotently(tmp_path: Path) -> None:
    store = InstallTransactionStore(tmp_path / "transactions", log_store=LogStore())
    transaction = store.open(workflow_id="workflow-a", capsule_fingerprint="sha256:abc")
    (transaction.dependency_envs_dir / "dep-env-a").mkdir()
    (transaction.model_blobs_dir / "model-a").mkdir()
    (transaction.dependency_envs_dir / "dep-env-a" / "manifest.json.tmp").write_text("partial", encoding="utf-8")
    store.lock_dir.mkdir(parents=True)
    (store.lock_dir / "dependency-env-a.lock").write_text("123", encoding="utf-8")

    first = store.sweep_startup()
    second = store.sweep_startup()

    assert first.stale_transactions_quarantined == 1
    assert first.stale_tmp_files_removed == 1
    assert first.stale_lock_files_removed == 1
    assert second.stale_transactions_quarantined == 0
    assert second.stale_tmp_files_removed == 0
    assert second.stale_lock_files_removed == 0
    marker = json.loads(transaction.quarantine_path.read_text(encoding="utf-8"))
    assert marker["status"] == "quarantined"
    assert marker["workflow_id"] == "workflow-a"
    assert transaction.model_blobs_dir.exists()
    assert not (transaction.dependency_envs_dir / "dep-env-a" / "manifest.json.tmp").exists()


def test_startup_sweep_removes_expired_quarantine(tmp_path: Path) -> None:
    store = InstallTransactionStore(tmp_path / "transactions", log_store=LogStore())
    transaction = store.open(workflow_id="workflow-a", capsule_fingerprint="sha256:abc")
    store.quarantine(transaction, reason="failed smoke")
    expired = datetime.now(UTC) - timedelta(seconds=1)
    marker = json.loads(transaction.quarantine_path.read_text(encoding="utf-8"))
    marker["retain_until"] = expired.isoformat()
    transaction.quarantine_path.write_text(json.dumps(marker), encoding="utf-8")

    report = store.sweep_startup()

    assert report.expired_quarantines_removed == 1
    assert not transaction.root_dir.exists()


def test_startup_sweep_removes_legacy_unscoped_transaction_dirs(tmp_path: Path) -> None:
    store = InstallTransactionStore(tmp_path / "transactions", log_store=LogStore())
    legacy_model = store.root_dir / "model-old-1234"
    legacy_dependency = store.root_dir / "dep-resolve-old-1234"
    unrelated = store.root_dir / "manual-debug"
    legacy_model.mkdir(parents=True)
    legacy_dependency.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    (legacy_model / "blob.tmp").write_text("partial", encoding="utf-8")

    report = store.sweep_startup()

    assert report.stale_tmp_files_removed == 1
    assert report.stale_unscoped_transactions_removed == 2
    assert not legacy_model.exists()
    assert not legacy_dependency.exists()
    assert unrelated.exists()
