"""Capsule install pipeline for Noofy Verified workflows.

The installer drives an `InstallState` through preparing -> downloading
required models -> checking compatibility -> ready, persisting after every
transition. A failure in any stage records the error on the install state
and leaves the status as `FAILED`; the workflow is never marked READY when
any prerequisite (model, env, smoke check) failed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.runtime.custom_nodes import (
    CustomNodeMaterializationError,
    CustomNodeMaterializationErrorCode,
)
from app.engine.diagnostics import LogStore
from app.runtime.dependency_lock import DependencyPolicyError
from app.runtime.dependency_env import DependencyEnvironmentInstallError
from app.runtime.install_state import (
    InstallStateStore,
    now_iso,
    user_facing_install_message,
)
from app.runtime.isolation import (
    CapsuleLock,
    InstallState,
    InstallStatus,
    InstalledModelReference,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestStatus,
    SmokeTestReport,
    TrustLevel,
)
from app.runtime.model_store import (
    LocalModelCandidateError,
    LocalModelRequirement,
    ModelDownloadError,
    ModelStore,
)
from app.runtime.profiles import RuntimeProfileResolutionError
from app.runtime.smoke_test import RunnerSmokeTestError
from app.runtime.workspace_preparer import PreparedRuntimeWorkspace, RuntimeWorkspacePreparer
from app.trust import capsule_source_policy

WorkspaceSmokeTest = Callable[[CapsuleLock, PreparedRuntimeWorkspace], Awaitable[SmokeTestReport | None]]


class CapsuleInstallError(RuntimeError):
    """Raised when the installer cannot prepare a capsule automatically."""

    def __init__(self, message: str, *, state: InstallState) -> None:
        super().__init__(message)
        self.state = state


class CapsuleInstaller:
    def __init__(
        self,
        *,
        install_state_store: InstallStateStore,
        model_store: ModelStore,
        workspace_preparer: RuntimeWorkspacePreparer | None = None,
        workspace_smoke_test: WorkspaceSmokeTest | None = None,
        log_store: LogStore | None = None,
    ) -> None:
        self.install_state_store = install_state_store
        self.model_store = model_store
        self.workspace_preparer = workspace_preparer
        self.workspace_smoke_test = workspace_smoke_test
        self.log_store = log_store or LogStore()

    async def prepare(
        self,
        capsule_lock: CapsuleLock,
        *,
        local_model_requirements: list[LocalModelRequirement] | None = None,
        workflow_execution_smoke_allowed: bool = True,
    ) -> InstallState:
        fingerprint = capsule_lock.runtime.capsule_fingerprint
        workflow_id = capsule_lock.workflow.package_id
        install_transaction = (
            self.workspace_preparer.install_transaction_store.open(
                workflow_id=workflow_id,
                capsule_fingerprint=fingerprint,
            )
            if self.workspace_preparer is not None
            else None
        )

        self._transition(fingerprint, InstallStatus.PREPARING, workflow_id, last_error=None)
        source_policy = capsule_source_policy(capsule_lock)
        if (
            not _trust_level_can_prepare_dependencies(capsule_lock.workflow.trust_level)
            or not _trust_level_can_prepare_dependencies(capsule_lock.trust.level)
            or not source_policy.automatic_preparation_allowed
        ):
            state = self._transition(
                fingerprint,
                InstallStatus.BLOCKED_BY_POLICY,
                workflow_id,
                last_error="This workflow is blocked by the source policy.",
            )
            if install_transaction is not None:
                self.workspace_preparer.install_transaction_store.quarantine(
                    install_transaction,
                    reason=state.last_error or "Blocked by policy",
                )
            raise CapsuleInstallError(state.last_error or "Unsupported capsule", state=state)

        self._transition(fingerprint, InstallStatus.DOWNLOADING, workflow_id)

        model_references: list[InstalledModelReference] = []
        model_view_path = None
        model_view = None
        try:
            for model_lock in capsule_lock.models:
                self.log_store.add(
                    "info",
                    "Preparing model",
                    "capsule.installer",
                    workflow_id=workflow_id,
                    details={
                        "model_id": model_lock.id,
                        "comfyui_folder": model_lock.comfyui_folder,
                        "filename": model_lock.filename,
                    },
                )
            if capsule_lock.models or local_model_requirements:
                self._transition(fingerprint, InstallStatus.MATERIALIZING_MODEL_VIEW, workflow_id)
                model_view = await self.model_store.materialize_model_view(
                    view_id=fingerprint,
                    model_locks=capsule_lock.models,
                    local_model_requirements=local_model_requirements,
                    staged_views_dir=install_transaction.model_views_dir if install_transaction is not None else None,
                    staged_blobs_dir=install_transaction.model_blobs_dir if install_transaction is not None else None,
                    source_policy=source_policy,
                )
                model_references = model_view.model_references
                model_view_path = model_view.view_path
        except LocalModelCandidateError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.CANNOT_PREPARE_AUTOMATICALLY,
                workflow_id,
                last_error=str(exc),
            )
            if install_transaction is not None:
                self.workspace_preparer.install_transaction_store.quarantine(install_transaction, reason=str(exc))
            raise CapsuleInstallError(str(exc), state=state) from exc
        except ModelDownloadError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.FAILED,
                workflow_id,
                last_error=str(exc),
            )
            if install_transaction is not None:
                self.workspace_preparer.install_transaction_store.quarantine(install_transaction, reason=str(exc))
            raise CapsuleInstallError(str(exc), state=state) from exc

        self._transition(fingerprint, InstallStatus.RESOLVING_DEPENDENCIES, workflow_id)
        prepared_workspace = None
        try:
            if self.workspace_preparer is not None:
                prepared_workspace = self.workspace_preparer.prepare(
                    capsule_lock,
                    model_view_dir=model_view_path,
                    model_view_fingerprint=model_view.view_fingerprint if model_view is not None else None,
                    install_transaction=install_transaction,
                )
        except RuntimeProfileResolutionError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.UNSUPPORTED_RUNTIME_PROFILE,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc
        except DependencyEnvironmentInstallError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.BLOCKED_BY_POLICY,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc
        except DependencyPolicyError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.BLOCKED_BY_POLICY,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc
        except CustomNodeMaterializationError as exc:
            status = (
                InstallStatus.CANNOT_PREPARE_AUTOMATICALLY
                if exc.code is CustomNodeMaterializationErrorCode.UNKNOWN_NODE_TYPE
                else InstallStatus.BLOCKED_BY_POLICY
            )
            state = self._transition(
                fingerprint,
                status,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc
        except Exception as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.FAILED,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc

        self._transition(fingerprint, InstallStatus.CHECKING_COMPATIBILITY, workflow_id)
        smoke_test_status = SmokeTestStatus.NOT_RUN
        smoke_test_report = SmokeTestReport()
        if prepared_workspace is not None:
            self._transition(fingerprint, InstallStatus.SMOKE_TESTING, workflow_id)
            try:
                smoke_test_report = await self._run_workspace_smoke_test(
                    capsule_lock,
                    prepared_workspace,
                    workflow_execution_smoke_allowed=workflow_execution_smoke_allowed,
                )
                self._write_transaction_smoke_report(prepared_workspace, smoke_test_report)
            except RunnerSmokeTestError as exc:
                smoke_test_report = exc.report
                self._write_transaction_smoke_report(prepared_workspace, smoke_test_report)
                self._quarantine_failed_workspace(
                    prepared_workspace,
                    workflow_id=workflow_id,
                    reason=str(exc),
                )
                state = self._transition(
                    fingerprint,
                    InstallStatus.FAILED,
                    workflow_id,
                    last_error=str(exc),
                    smoke_test_status=SmokeTestStatus.FAILED,
                    smoke_test_report=smoke_test_report,
                )
                raise CapsuleInstallError(str(exc), state=state) from exc
            except Exception as exc:
                self._quarantine_failed_workspace(
                    prepared_workspace,
                    workflow_id=workflow_id,
                    reason=str(exc),
                )
                state = self._transition(
                    fingerprint,
                    InstallStatus.FAILED,
                    workflow_id,
                    last_error=str(exc),
                    smoke_test_status=SmokeTestStatus.FAILED,
                    smoke_test_report=smoke_test_report,
                )
                raise CapsuleInstallError(str(exc), state=state) from exc
            if _smoke_report_has_failed_stage(smoke_test_report):
                failure_message = _smoke_report_failure_message(smoke_test_report)
                self._write_transaction_smoke_report(prepared_workspace, smoke_test_report)
                self._quarantine_failed_workspace(
                    prepared_workspace,
                    workflow_id=workflow_id,
                    reason=failure_message,
                )
                state = self._transition(
                    fingerprint,
                    InstallStatus.FAILED,
                    workflow_id,
                    last_error=failure_message,
                    smoke_test_status=SmokeTestStatus.FAILED,
                    smoke_test_report=smoke_test_report,
                )
                raise CapsuleInstallError(state.last_error or "Smoke test failed", state=state)
            if _required_smoke_stages_passed(capsule_lock, smoke_test_report):
                smoke_test_status = SmokeTestStatus.PASSED

        if prepared_workspace is not None and self.workspace_preparer is not None and smoke_test_status is SmokeTestStatus.PASSED:
            if model_view is not None and model_view.is_staged:
                model_view = self.model_store.promote_model_view(model_view)
                model_references = model_view.model_references
                model_view_path = model_view.view_path
                self.workspace_preparer.replace_staged_model_view(
                    prepared_workspace,
                    model_view_dir=model_view_path,
                    workflow_id=workflow_id,
                )
            prepared_workspace = self.workspace_preparer.mark_ready(
                prepared_workspace,
                smoke_test_status=smoke_test_status,
                workflow_id=workflow_id,
            )

        ready_fields = _prepared_runtime_fields(
            capsule_lock,
            prepared_workspace,
            smoke_test_status=smoke_test_status,
            smoke_test_report=smoke_test_report,
            model_references=model_references,
        )
        ready_fields["installed_at"] = now_iso()

        if (prepared_workspace is None and capsule_lock.custom_nodes) or (
            prepared_workspace is not None and smoke_test_status is not SmokeTestStatus.PASSED
        ):
            return self._transition(
                fingerprint,
                InstallStatus.PREPARED_NEEDS_INPUT_SETUP,
                workflow_id,
                **ready_fields,
            )

        return self._transition(
            fingerprint,
            InstallStatus.READY,
            workflow_id,
            **ready_fields,
        )

    def get_state(self, capsule_lock: CapsuleLock) -> InstallState:
        return self.install_state_store.get_or_create(capsule_lock.runtime.capsule_fingerprint)

    async def _run_workspace_smoke_test(
        self,
        capsule_lock: CapsuleLock,
        prepared_workspace: PreparedRuntimeWorkspace,
        *,
        workflow_execution_smoke_allowed: bool,
    ) -> SmokeTestReport:
        if self.workspace_smoke_test is None:
            return _with_source_policy_smoke_diagnostics(
                SmokeTestReport(
                    dependency_env=SmokeStageResult(
                        status=SmokeStageStatus.BLOCKED,
                        message="No dependency environment smoke check is configured.",
                    ),
                    custom_node_import=_custom_node_stage_result(capsule_lock),
                    runner_health=SmokeStageResult(
                        status=SmokeStageStatus.BLOCKED,
                        message="No runner health smoke check is configured.",
                    ),
                    workflow_execution=SmokeStageResult(
                        status=SmokeStageStatus.BLOCKED,
                        message="No workflow execution smoke check is configured.",
                    ),
                ),
                capsule_lock,
            )

        try:
            report = await self.workspace_smoke_test(capsule_lock, prepared_workspace)
        except RunnerSmokeTestError as exc:
            raise RunnerSmokeTestError(
                str(exc),
                report=_with_source_policy_smoke_diagnostics(exc.report, capsule_lock),
            ) from exc
        smoke_report = report or SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=_custom_node_stage_result(capsule_lock),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(
                status=SmokeStageStatus.BLOCKED,
                message="Smoke test did not report workflow execution.",
            ),
        )
        if not workflow_execution_smoke_allowed:
            smoke_report = smoke_report.model_copy(
                update={
                    "workflow_execution": SmokeStageResult(
                        status=SmokeStageStatus.BLOCKED,
                        message="Workflow execution smoke is blocked by unresolved runtime inputs.",
                    )
                }
            )
        return _with_source_policy_smoke_diagnostics(smoke_report, capsule_lock)

    def _quarantine_failed_workspace(
        self,
        prepared_workspace: PreparedRuntimeWorkspace,
        *,
        workflow_id: str,
        reason: str,
    ) -> None:
        if self.workspace_preparer is None:
            return
        self.workspace_preparer.quarantine_failed(
            prepared_workspace,
            workflow_id=workflow_id,
            reason=reason,
        )

    def _write_transaction_smoke_report(
        self,
        prepared_workspace: PreparedRuntimeWorkspace,
        report: SmokeTestReport,
    ) -> None:
        if self.workspace_preparer is None or prepared_workspace.install_transaction is None:
            return
        self.workspace_preparer.install_transaction_store.write_smoke_report(
            prepared_workspace.install_transaction,
            report=report.model_dump(mode="json"),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition(
        self,
        fingerprint: str,
        status: InstallStatus,
        workflow_id: str,
        **fields,
    ) -> InstallState:
        state = self.install_state_store.update(fingerprint, status=status, **fields)
        level = "error" if status in {
            InstallStatus.FAILED,
            InstallStatus.UNSUPPORTED,
            InstallStatus.UNSUPPORTED_RUNTIME_PROFILE,
            InstallStatus.BLOCKED_BY_POLICY,
            InstallStatus.CANNOT_PREPARE_AUTOMATICALLY,
        } else "info"
        self.log_store.add(
            level,
            f"Capsule install: {user_facing_install_message(status)}",
            "capsule.installer",
            workflow_id=workflow_id,
            details={
                "capsule_fingerprint": fingerprint,
                "status": status.value,
                "last_error": state.last_error,
            },
        )
        return state


def _prepared_runtime_fields(
    capsule_lock: CapsuleLock,
    prepared_workspace: PreparedRuntimeWorkspace | None,
    *,
    smoke_test_status: SmokeTestStatus,
    smoke_test_report: SmokeTestReport,
    model_references: list[InstalledModelReference],
) -> dict[str, object]:
    ready_fields: dict[str, object] = {
        "model_references": model_references,
        "smoke_test_status": smoke_test_status,
        "smoke_test_report": smoke_test_report,
    }
    if prepared_workspace is not None:
        ready_fields["runtime_profile_variant_id"] = capsule_lock.runtime.runtime_profile_variant_id
        ready_fields["runtime_profile_manifest_hash"] = capsule_lock.runtime.runtime_profile_manifest_hash
        ready_fields["runtime_profile_catalog_version"] = capsule_lock.runtime.runtime_profile_catalog_version
        ready_fields["dependency_env_fingerprint"] = prepared_workspace.dependency_env_manifest.fingerprint
        ready_fields["runner_workspace_fingerprint"] = prepared_workspace.runner_workspace_manifest.fingerprint
        ready_fields["runner_process_compatibility_key"] = capsule_lock.runtime.runner_process_compatibility_key
        ready_fields["dependency_env_path"] = str(prepared_workspace.dependency_env_path)
        ready_fields["runner_workspace_path"] = str(prepared_workspace.runner_workspace_path)
    return ready_fields


def _custom_node_stage_result(capsule_lock: CapsuleLock) -> SmokeStageResult:
    if capsule_lock.custom_nodes:
        return SmokeStageResult(
            status=SmokeStageStatus.NOT_RUN,
            message="Custom node import smoke has not run.",
        )
    return SmokeStageResult(
        status=SmokeStageStatus.SKIPPED,
        message="Workflow has no custom nodes.",
    )


def _with_source_policy_smoke_diagnostics(
    report: SmokeTestReport,
    capsule_lock: CapsuleLock,
) -> SmokeTestReport:
    source_policy = capsule_source_policy(capsule_lock).model_dump(mode="json", exclude_none=True)
    updates: dict[str, SmokeStageResult] = {}
    for stage_name in ("dependency_env", "custom_node_import", "runner_health", "workflow_execution"):
        stage = getattr(report, stage_name)
        if stage.status not in {SmokeStageStatus.BLOCKED, SmokeStageStatus.FAILED}:
            continue
        updates[stage_name] = stage.model_copy(
            update={"details": {**stage.details, "source_policy": source_policy}}
        )
    if not updates:
        return report
    return report.model_copy(update=updates)


def _required_smoke_stages_passed(
    capsule_lock: CapsuleLock,
    report: SmokeTestReport,
) -> bool:
    if report.dependency_env.status is not SmokeStageStatus.PASSED:
        return False
    if report.runner_health.status is not SmokeStageStatus.PASSED:
        return False
    if report.workflow_execution.status is not SmokeStageStatus.PASSED:
        return False
    if capsule_lock.custom_nodes and report.custom_node_import.status is not SmokeStageStatus.PASSED:
        return False
    return True


def _smoke_report_has_failed_stage(report: SmokeTestReport) -> bool:
    return any(
        stage.status is SmokeStageStatus.FAILED
        for stage in (
            report.dependency_env,
            report.custom_node_import,
            report.runner_health,
            report.workflow_execution,
        )
    )


def _smoke_report_failure_message(report: SmokeTestReport) -> str:
    for stage_name, stage in (
        ("dependency environment", report.dependency_env),
        ("custom node import", report.custom_node_import),
        ("runner health", report.runner_health),
        ("workflow execution", report.workflow_execution),
    ):
        if stage.status is SmokeStageStatus.FAILED:
            return stage.message or f"{stage_name} smoke test failed"
    return "Smoke test failed"


def _trust_level_can_prepare_dependencies(trust_level: TrustLevel) -> bool:
    return trust_level in {
        TrustLevel.NOOFY_VERIFIED,
        TrustLevel.REGISTRY_LOCKED,
        TrustLevel.QUARANTINED_COMMUNITY,
    }
