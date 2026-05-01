"""Capsule install pipeline for Noofy Verified workflows.

The installer drives an `InstallState` through preparing -> downloading
required models -> checking compatibility -> ready, persisting after every
transition. A failure in any stage records the error on the install state
and leaves the status as `FAILED`; the workflow is never marked READY when
any prerequisite (model, env, smoke check) failed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.engine.diagnostics import LogStore
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
    SmokeTestStatus,
    TrustLevel,
)
from app.runtime.model_store import ModelDownloadError, ModelStore
from app.runtime.profiles import RuntimeProfileResolutionError
from app.runtime.workspace_preparer import PreparedRuntimeWorkspace, RuntimeWorkspacePreparer

WorkspaceSmokeTest = Callable[[CapsuleLock, PreparedRuntimeWorkspace], Awaitable[None]]


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

    async def prepare(self, capsule_lock: CapsuleLock) -> InstallState:
        fingerprint = capsule_lock.runtime.capsule_fingerprint
        workflow_id = capsule_lock.workflow.package_id

        self._transition(fingerprint, InstallStatus.PREPARING, workflow_id, last_error=None)
        if (
            capsule_lock.workflow.trust_level is not TrustLevel.NOOFY_VERIFIED
            or capsule_lock.trust.level is not TrustLevel.NOOFY_VERIFIED
            or capsule_lock.custom_nodes
        ):
            state = self._transition(
                fingerprint,
                InstallStatus.FAILED,
                workflow_id,
                last_error="This workflow cannot be prepared by the verified core installer.",
            )
            raise CapsuleInstallError(state.last_error or "Unsupported capsule", state=state)

        self._transition(fingerprint, InstallStatus.DOWNLOADING, workflow_id)

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
                await self.model_store.materialize(model_lock)
        except ModelDownloadError as exc:
            state = self._transition(
                fingerprint,
                InstallStatus.FAILED,
                workflow_id,
                last_error=str(exc),
            )
            raise CapsuleInstallError(str(exc), state=state) from exc

        self._transition(fingerprint, InstallStatus.RESOLVING_DEPENDENCIES, workflow_id)
        prepared_workspace = None
        try:
            if self.workspace_preparer is not None:
                prepared_workspace = self.workspace_preparer.prepare(capsule_lock)
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
        if prepared_workspace is not None and self.workspace_smoke_test is not None:
            try:
                await self.workspace_smoke_test(capsule_lock, prepared_workspace)
            except Exception as exc:
                state = self._transition(
                    fingerprint,
                    InstallStatus.FAILED,
                    workflow_id,
                    last_error=str(exc),
                    smoke_test_status=SmokeTestStatus.FAILED,
                )
                raise CapsuleInstallError(str(exc), state=state) from exc
            smoke_test_status = SmokeTestStatus.PASSED

        if prepared_workspace is not None and self.workspace_preparer is not None:
            prepared_workspace = self.workspace_preparer.mark_ready(
                prepared_workspace,
                smoke_test_status=smoke_test_status,
                workflow_id=workflow_id,
            )

        ready_fields = {
            "installed_at": now_iso(),
            "smoke_test_status": smoke_test_status,
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

        return self._transition(
            fingerprint,
            InstallStatus.READY,
            workflow_id,
            **ready_fields,
        )

    def get_state(self, capsule_lock: CapsuleLock) -> InstallState:
        return self.install_state_store.get_or_create(capsule_lock.runtime.capsule_fingerprint)

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
