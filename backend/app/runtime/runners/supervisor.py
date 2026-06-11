"""Runner selection and per-job routing.

Phase 2 of the runtime isolation plan introduces an explicit `RunnerSupervisor`
that owns the set of runner processes the backend can talk to and the mapping
between an in-flight job and the runner that accepted it. Today the supervisor
exposes the single core ComfyUI runner that already exists; later phases will
add isolated, per-capsule runner workspaces and use the same surface to switch
endpoints per workflow.

The runtime side intentionally stays minimal here: the supervisor does not start
or stop runner processes. That responsibility still belongs to `RuntimeManager`
for the core runner. The supervisor only tracks descriptors, adapters and the
job -> runner registry the engine service uses to route progress, cancel, and
result lookups.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.engine.adapter import EngineAdapter

CORE_RUNNER_ID = "core"
CORE_RUNNER_FINGERPRINT = "core"


class RunnerStatus(StrEnum):
    UNKNOWN = "unknown"
    MISSING_RUNTIME = "missing_runtime"
    PREPARING = "preparing"
    STARTING = "starting"
    READY = "ready"
    IDLE = "idle"
    RUNNING = "running"
    QUEUED = "queued"
    QUEUED_PENDING_SWITCH = "queued_pending_switch"
    QUEUED_PENDING_MEMORY = "queued_pending_memory"
    IDLE_WARM = "idle_warm"
    RESERVING = "reserving"
    SUBMITTING = "submitting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SWITCHING = "switching"
    EVICTING_RUNNER = "evicting_runner"
    WAITING_FOR_MEMORY_RELEASE = "waiting_for_memory_release"
    LOADING_MODEL = "loading_model"
    RETRYING_AFTER_MEMORY_CLEANUP = "retrying_after_memory_cleanup"
    FAILED = "failed"
    BLOCKED_BY_MEMORY = "blocked_by_memory"
    MEMORY_CLEANUP_FAILED = "memory_cleanup_failed"
    RELEASE_FAILED = "release_failed"
    EVICTED_FOR_MEMORY = "evicted_for_memory"
    EVICTED_AFTER_COOLDOWN = "evicted_after_cooldown"
    CO_RESIDENT = "co_resident"
    UNREACHABLE = "unreachable"


class RunnerKind(StrEnum):
    CORE_COMFYUI = "core_comfyui"
    ISOLATED_COMFYUI = "isolated_comfyui"


class RunnerMemoryClass(StrEnum):
    GPU_HEAVY = "gpu_heavy"
    GPU_MEDIUM = "gpu_medium"
    GPU_LIGHT = "gpu_light"
    CPU_ONLY = "cpu_only"
    UNKNOWN = "unknown"


class RunnerMemoryEstimateConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class RunnerMemoryEstimateSource(StrEnum):
    DECLARED = "declared"
    CREATOR_OBSERVED = "creator_observed"
    LOCAL_OBSERVED = "local_observed"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"


class RunnerSelectionAction(StrEnum):
    REUSE = "reuse"
    SWITCH = "switch"
    QUEUE_PENDING_SWITCH = "queue_pending_switch"
    START_NEW = "start_new"
    BLOCKED_BY_MEMORY = "blocked_by_memory"


class QueuedRunnerStartKind(StrEnum):
    PENDING_SWITCH = "pending_switch"
    PENDING_MEMORY = "pending_memory"


class QueuedRunnerStartStatus(StrEnum):
    QUEUED = "queued"
    HANDING_OFF = "handing_off"
    SUBMITTED = "submitted"
    REQUEUED = "requeued"
    FAILED = "failed"
    CANCELED = "canceled"


class RunnerReservationKind(StrEnum):
    SUBMISSION = "submission"
    EVICTION = "eviction"
    STARTUP = "startup"


class RunnerDescriptor(BaseModel):
    """Serializable view of a runner managed by the supervisor."""

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    kind: RunnerKind
    base_url: str = Field(min_length=1)
    ws_url: str | None = None
    fingerprint: str = Field(min_length=1)
    status: RunnerStatus = RunnerStatus.UNKNOWN
    runner_workspace_fingerprint: str | None = None
    dependency_env_fingerprint: str | None = None
    runner_process_compatibility_key: str | None = None
    runner_workspace_path: str | None = None
    model_view_fingerprint: str | None = None
    runtime_profile_id: str | None = None
    runtime_profile_variant_id: str | None = None
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    memory_estimate_confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.UNKNOWN
    memory_estimate_source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.UNKNOWN
    observed_idle_vram_mb: int | None = Field(default=None, ge=0)
    observed_idle_ram_mb: int | None = Field(default=None, ge=0)
    observed_load_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_load_peak_ram_mb: int | None = Field(default=None, ge=0)
    observed_execution_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_execution_peak_ram_mb: int | None = Field(default=None, ge=0)
    recent_memory_error_at: str | None = None
    recent_memory_error_count: int = Field(default=0, ge=0)
    pid: int | None = None
    memory_telemetry_path: str | None = None
    current_job_id: str | None = None
    current_workflow_id: str | None = None
    last_workflow_id: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, object] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    last_used_at: str | None = None
    open_workflow_lease_count: int = 0
    open_workflow_lease_ids: list[str] = Field(default_factory=list)
    output_stream_lease_count: int = Field(default=0, ge=0)
    closed_view_cooldown_expires_at: str | None = None
    reservation_token: str | None = None
    reservation_kind: RunnerReservationKind | None = None


class WorkflowLeaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    opened_at: datetime
    last_heartbeat_at: datetime


class ExpiredWorkflowLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    runner: RunnerDescriptor


class RunnerSelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RunnerSelectionAction
    runner_id: str | None = None
    evict_runner_id: str | None = None
    queued_behind_runner_id: str | None = None
    reason: str | None = None


class QueuedRunnerStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    kind: QueuedRunnerStartKind
    status: QueuedRunnerStartStatus = QueuedRunnerStartStatus.QUEUED
    queued_behind_runner_id: str | None = None
    reason: str | None = None
    created_at: str
    updated_at: str | None = None
    canceled_at: str | None = None
    cancel_requested: bool = False
    attempt_count: int = Field(default=0, ge=0)
    last_reason: str | None = None


class RunnerReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    runner_id: str = Field(min_length=1)
    kind: RunnerReservationKind
    prior_status: RunnerStatus
    workflow_id: str | None = None
    created_at: str


class RunnerNotFoundError(LookupError):
    """Raised when a runner_id is not known to the supervisor."""


class JobRunnerNotFoundError(LookupError):
    """Raised when a job has not been routed to any runner."""


class DuplicateJobRegistrationError(RuntimeError):
    """Raised when a job id is registered more than once."""


class JobRunnerRegistry:
    """Maps job ids to the runner that accepted them.

    The engine service registers a job after submitting it to a runner, then
    looks the runner back up when progress, cancel, or result calls arrive on
    the API. Keeping this in its own object makes it easy to replace the
    in-memory implementation with persistent storage later.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[str, str] = {}

    def register(self, job_id: str, runner_id: str) -> None:
        with self._lock:
            if job_id in self._by_job:
                raise DuplicateJobRegistrationError(f"Job is already registered: {job_id}")
            self._by_job[job_id] = runner_id

    def runner_for(self, job_id: str) -> str | None:
        with self._lock:
            return self._by_job.get(job_id)

    def unregister(self, job_id: str) -> None:
        with self._lock:
            self._by_job.pop(job_id, None)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._by_job)


class RunnerSupervisor:
    """Selects and tracks runner processes for the engine service.

    Phase 2 only exposes the core ComfyUI runner that already exists, but the
    surface (`acquire_runner`, `register_job`, `adapter_for_job`) is what later
    phases need to route a workflow at runtime to its own isolated runner.
    """

    def __init__(
        self,
        *,
        closed_view_cooldown_seconds: float = 90,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._descriptors: dict[str, RunnerDescriptor] = {}
        self._adapters: dict[str, EngineAdapter] = {}
        self._core_runner_id: str | None = None
        self._workflow_runners: dict[str, str] = {}
        self._workflow_leases: dict[str, WorkflowLeaseRecord] = {}
        self._queued_runner_starts: dict[str, QueuedRunnerStart] = {}
        self._runtime_activation_in_progress = False
        self._runner_starts_in_progress: dict[str, int] = {}
        self._workflow_preparations_in_progress: dict[str, int] = {}
        self._reservations: dict[str, RunnerReservation] = {}
        self._registry = JobRunnerRegistry()
        self._state_change_notifier: Callable[[str], None] | None = None
        self._terminal_notifier: Callable[[str], None] | None = None
        self.closed_view_cooldown_seconds = closed_view_cooldown_seconds
        self._now = now or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_core_runner(self, descriptor: RunnerDescriptor, adapter: EngineAdapter) -> None:
        with self._lock:
            if self._core_runner_id is not None:
                raise RuntimeError("Core runner is already registered")
            if descriptor.kind is not RunnerKind.CORE_COMFYUI:
                raise ValueError("Core runner must use kind=core_comfyui")
            self._descriptors[descriptor.runner_id] = descriptor
            self._adapters[descriptor.runner_id] = adapter
            self._core_runner_id = descriptor.runner_id
        self._configure_adapter_terminal_notifier(adapter)

    def configure_state_change_notifier(self, notifier: Callable[[str], None] | None) -> None:
        self._state_change_notifier = notifier

    def configure_terminal_notifier(self, notifier: Callable[[str], None] | None) -> None:
        self._terminal_notifier = notifier
        with self._lock:
            adapters = list(self._adapters.values())
        for adapter in adapters:
            self._configure_adapter_terminal_notifier(adapter)

    def upsert_runner(self, descriptor: RunnerDescriptor, adapter: EngineAdapter) -> None:
        """Register or replace a non-core runner descriptor and adapter."""
        if descriptor.kind is RunnerKind.CORE_COMFYUI:
            raise ValueError("Core runner must be registered with register_core_runner")
        with self._lock:
            if descriptor.runner_id == self._core_runner_id:
                raise ValueError("Cannot replace the core runner through upsert_runner")
            self._descriptors[descriptor.runner_id] = descriptor
            self._adapters[descriptor.runner_id] = adapter
        self._configure_adapter_terminal_notifier(adapter)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @property
    def job_registry(self) -> JobRunnerRegistry:
        return self._registry

    def list_runners(self) -> list[RunnerDescriptor]:
        with self._lock:
            return list(self._descriptors.values())

    def begin_runtime_activation(self) -> list[str]:
        """Atomically block new submissions when no runner work is in flight."""
        with self._lock:
            busy_runner_ids = sorted(
                {
                    *self._runner_starts_in_progress,
                    *(
                        f"prepare:{workflow_id}"
                        for workflow_id in self._workflow_preparations_in_progress
                    ),
                    *(
                        runner.runner_id
                        for runner in self._descriptors.values()
                        if (
                            runner.current_job_id is not None
                            or runner.reservation_token is not None
                            or runner.output_stream_lease_count > 0
                            or runner.status
                            in {
                                RunnerStatus.RUNNING,
                                RunnerStatus.RESERVING,
                                RunnerStatus.SUBMITTING,
                            }
                        )
                    ),
                }
            )
            if self._runtime_activation_in_progress:
                return ["runtime_activation_in_progress"]
            if busy_runner_ids:
                return busy_runner_ids
            self._runtime_activation_in_progress = True
        self._notify_state_change("runtime_activation_started")
        return []

    def end_runtime_activation(self) -> None:
        with self._lock:
            was_active = self._runtime_activation_in_progress
            self._runtime_activation_in_progress = False
        if was_active:
            self._notify_state_change("runtime_activation_finished")

    def runtime_activation_in_progress(self) -> bool:
        with self._lock:
            return self._runtime_activation_in_progress

    def begin_runner_start(self, runner_id: str) -> bool:
        """Reserve a process startup so activation cannot race it."""
        with self._lock:
            if self._runtime_activation_in_progress:
                return False
            self._runner_starts_in_progress[runner_id] = (
                self._runner_starts_in_progress.get(runner_id, 0) + 1
            )
        return True

    def end_runner_start(self, runner_id: str) -> None:
        with self._lock:
            count = self._runner_starts_in_progress.get(runner_id, 0)
            if count <= 1:
                self._runner_starts_in_progress.pop(runner_id, None)
            else:
                self._runner_starts_in_progress[runner_id] = count - 1

    def begin_workflow_preparation(self, workflow_id: str) -> bool:
        """Reserve profile/source use so activation cannot race preparation."""
        with self._lock:
            if self._runtime_activation_in_progress:
                return False
            self._workflow_preparations_in_progress[workflow_id] = (
                self._workflow_preparations_in_progress.get(workflow_id, 0) + 1
            )
        return True

    def end_workflow_preparation(self, workflow_id: str) -> None:
        with self._lock:
            count = self._workflow_preparations_in_progress.get(workflow_id, 0)
            if count <= 1:
                self._workflow_preparations_in_progress.pop(workflow_id, None)
            else:
                self._workflow_preparations_in_progress[workflow_id] = count - 1

    def get_runner(self, runner_id: str) -> RunnerDescriptor:
        with self._lock:
            descriptor = self._descriptors.get(runner_id)
        if descriptor is None:
            raise RunnerNotFoundError(f"Unknown runner: {runner_id}")
        return descriptor

    def get_adapter(self, runner_id: str) -> EngineAdapter:
        with self._lock:
            adapter = self._adapters.get(runner_id)
        if adapter is None:
            raise RunnerNotFoundError(f"No adapter registered for runner: {runner_id}")
        return adapter

    def core_runner(self) -> RunnerDescriptor:
        with self._lock:
            runner_id = self._core_runner_id
        if runner_id is None:
            raise RunnerNotFoundError("Core runner has not been registered")
        return self.get_runner(runner_id)

    def acquire_runner(self, workflow_package: object) -> RunnerDescriptor:
        """Return the runner that should host `workflow_package`.

        A workflow can be explicitly bound to a resident runner. Busy transient
        states stay bound so a second submission queues behind the same runner
        instead of leaking onto the core fallback. If no binding exists, or the
        bound runner is genuinely unavailable, the core runner remains the
        conservative fallback.
        """
        workflow_id = self._workflow_id(workflow_package)
        if workflow_id is not None:
            bound_runner = self.runner_for_workflow(workflow_id)
            if bound_runner is not None and _runner_is_resident(bound_runner):
                return bound_runner
        return self.core_runner()

    def runner_for_workflow(self, workflow_id: str) -> RunnerDescriptor | None:
        with self._lock:
            runner_id = self._workflow_runners.get(workflow_id)
        if runner_id is None:
            return None
        return self.get_runner(runner_id)

    def workflows_bound_to_runner(self, runner_id: str) -> list[str]:
        with self._lock:
            return sorted(
                workflow_id
                for workflow_id, bound_runner_id in self._workflow_runners.items()
                if bound_runner_id == runner_id
            )

    def expired_closed_view_runners(self) -> list[RunnerDescriptor]:
        """Isolated runners whose last workflow view closed and cooldown elapsed.

        Returns only runners that are still resident-idle from the supervisor's
        perspective; activity and queued-demand safety checks belong to the
        caller, which must still take an eviction reservation before acting.
        """
        now = self._now()
        with self._lock:
            runners = list(self._descriptors.values())
        expired: list[RunnerDescriptor] = []
        for runner in runners:
            if runner.kind is not RunnerKind.ISOLATED_COMFYUI:
                continue
            if runner.open_workflow_lease_count > 0:
                continue
            expires_at = _parse_iso(runner.closed_view_cooldown_expires_at)
            if expires_at is None or expires_at > now:
                continue
            expired.append(runner)
        return expired

    def closed_view_cooldown_remaining_seconds(self, runner_id: str) -> float | None:
        descriptor = self.get_runner(runner_id)
        expires_at = _parse_iso(descriptor.closed_view_cooldown_expires_at)
        if expires_at is None:
            return None
        return max(0.0, (expires_at - self._now()).total_seconds())

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update_runner_endpoint(
        self,
        runner_id: str,
        base_url: str,
        ws_url: str | None = None,
    ) -> RunnerDescriptor:
        """Reconfigure a runner's endpoint (and its adapter) in lock-step."""
        adapter = self.get_adapter(runner_id)
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            new_descriptor = descriptor.model_copy(update={"base_url": base_url, "ws_url": ws_url})
            self._descriptors[runner_id] = new_descriptor
        adapter.configure_endpoint(base_url, ws_url)
        return new_descriptor

    def update_runner_status(self, runner_id: str, status: RunnerStatus) -> RunnerDescriptor:
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            new_descriptor = descriptor.model_copy(update={"status": status})
            self._descriptors[runner_id] = new_descriptor
        self._notify_state_change("runner_status_changed")
        return new_descriptor

    def fill_runner_memory_observation(
        self,
        runner_id: str,
        *,
        observed_execution_peak_vram_mb: int | None = None,
        observed_execution_peak_ram_mb: int | None = None,
        observed_memory_class: RunnerMemoryClass | None = None,
        observed_source: RunnerMemoryEstimateSource | None = None,
        observed_confidence: RunnerMemoryEstimateConfidence | None = None,
    ) -> RunnerDescriptor:
        """Fill missing best-effort memory observations without replacing stronger data."""
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            updates: dict[str, object] = {}
            if observed_execution_peak_vram_mb is not None:
                updates["observed_execution_peak_vram_mb"] = max(
                    descriptor.observed_execution_peak_vram_mb or 0,
                    observed_execution_peak_vram_mb,
                )
            if observed_execution_peak_ram_mb is not None:
                updates["observed_execution_peak_ram_mb"] = max(
                    descriptor.observed_execution_peak_ram_mb or 0,
                    observed_execution_peak_ram_mb,
                )
            # Refine the runner's classification from local observed evidence,
            # but never downgrade stronger evidence with weaker evidence.
            updates.update(
                _runner_memory_classification_update(
                    descriptor,
                    memory_class=observed_memory_class,
                    source=observed_source,
                    confidence=observed_confidence,
                )
            )
            if not updates:
                return descriptor
            updated = descriptor.model_copy(update=updates)
            self._descriptors[runner_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Atomic reservations
    # ------------------------------------------------------------------

    def reserve_runner_for_submission(
        self,
        runner_id: str,
        *,
        workflow_id: str | None = None,
    ) -> RunnerReservation | None:
        return self._reserve_runner(
            runner_id,
            kind=RunnerReservationKind.SUBMISSION,
            workflow_id=workflow_id,
            allowed_statuses={
                RunnerStatus.UNKNOWN,
                RunnerStatus.READY,
                RunnerStatus.IDLE,
                RunnerStatus.IDLE_WARM,
                RunnerStatus.CO_RESIDENT,
            },
            reserved_status=RunnerStatus.RESERVING,
        )

    def reserve_runner_for_eviction(self, runner_id: str) -> RunnerReservation | None:
        return self._reserve_runner(
            runner_id,
            kind=RunnerReservationKind.EVICTION,
            allowed_statuses={
                RunnerStatus.READY,
                RunnerStatus.IDLE,
                RunnerStatus.IDLE_WARM,
                RunnerStatus.CO_RESIDENT,
                RunnerStatus.RELEASE_FAILED,
            },
            reserved_status=RunnerStatus.EVICTING_RUNNER,
        )

    def reserve_runner_for_startup(self, runner_id: str) -> RunnerReservation | None:
        return self._reserve_runner(
            runner_id,
            kind=RunnerReservationKind.STARTUP,
            allowed_statuses={RunnerStatus.STOPPED, RunnerStatus.UNKNOWN, RunnerStatus.FAILED},
            reserved_status=RunnerStatus.RESERVING,
        )

    def runner_busy_with_workflow(self, runner_id: str, workflow_id: str) -> bool:
        """Whether the runner is running or handing off a submission for `workflow_id`."""
        with self._lock:
            descriptor = self._descriptors.get(runner_id)
            if descriptor is None:
                return False
            if descriptor.current_workflow_id == workflow_id:
                return True
            if descriptor.reservation_token is None:
                return False
            reservation = self._reservations.get(descriptor.reservation_token)
            return reservation is not None and reservation.workflow_id == workflow_id

    def mark_runner_submitting(self, token: str) -> RunnerDescriptor | None:
        return self._transition_reserved_runner(token, RunnerStatus.SUBMITTING)

    def mark_runner_waiting_for_memory_release(self, token: str) -> RunnerDescriptor | None:
        return self._transition_reserved_runner(token, RunnerStatus.WAITING_FOR_MEMORY_RELEASE)

    def fail_runner_memory_release(self, token: str) -> RunnerDescriptor | None:
        with self._lock:
            reservation = self._reservations.pop(token, None)
            if reservation is None:
                return None
            descriptor = self._descriptor_locked(reservation.runner_id)
            updated = descriptor.model_copy(
                update={
                    "status": RunnerStatus.RELEASE_FAILED,
                    "reservation_token": None,
                    "reservation_kind": None,
                    "last_used_at": _iso(self._now()),
                }
            )
            self._descriptors[reservation.runner_id] = updated
        self._notify_state_change("runner_memory_release_failed")
        return updated

    def rollback_runner_reservation(
        self,
        token: str,
        *,
        notify_state_change: bool = True,
    ) -> RunnerDescriptor | None:
        with self._lock:
            reservation = self._reservations.pop(token, None)
            if reservation is None:
                return None
            descriptor = self._descriptor_locked(reservation.runner_id)
            updated = descriptor.model_copy(
                update={
                    "status": reservation.prior_status,
                    "reservation_token": None,
                    "reservation_kind": None,
                }
            )
            self._descriptors[reservation.runner_id] = updated
        if notify_state_change:
            self._notify_state_change("runner_reservation_rolled_back")
        return updated

    def cancel_pre_submission_reservation(self, token: str) -> bool:
        """Rollback a reservation unless adapter submission is already awaiting.

        Once the adapter call has started, the returned job must be registered
        and canceled canonically so queue aliases remain valid.
        """
        with self._lock:
            reservation = self._reservations.get(token)
            if reservation is None:
                return True
            descriptor = self._descriptor_locked(reservation.runner_id)
            if descriptor.status is RunnerStatus.SUBMITTING:
                return False
            self._reservations.pop(token, None)
            self._descriptors[reservation.runner_id] = descriptor.model_copy(
                update={
                    "status": reservation.prior_status,
                    "reservation_token": None,
                    "reservation_kind": None,
                }
            )
        self._notify_state_change("runner_reservation_canceled")
        return True

    def commit_runner_submission(
        self,
        token: str,
        *,
        job_id: str,
        workflow_id: str | None = None,
        model_residency_signature: str | None = None,
        model_residency_payload: dict[str, object] | None = None,
        execution_profile_signature: str | None = None,
        memory_signatures_known: bool = False,
        memory_class: RunnerMemoryClass | None = None,
        memory_estimate_source: RunnerMemoryEstimateSource | None = None,
        memory_estimate_confidence: RunnerMemoryEstimateConfidence | None = None,
    ) -> RunnerDescriptor:
        with self._lock:
            reservation = self._reservations.pop(token, None)
            if reservation is None or reservation.kind is not RunnerReservationKind.SUBMISSION:
                raise RuntimeError(f"Unknown submission reservation: {token}")
            descriptor = self._descriptor_locked(reservation.runner_id)
            updates: dict[str, object] = {
                "status": RunnerStatus.RUNNING,
                "current_job_id": job_id,
                "current_workflow_id": workflow_id,
                "last_workflow_id": workflow_id or descriptor.last_workflow_id,
                "last_used_at": _iso(self._now()),
                "reservation_token": None,
                "reservation_kind": None,
            }
            if memory_signatures_known:
                updates["model_residency_signature"] = model_residency_signature
                updates["model_residency_payload"] = dict(model_residency_payload or {})
                updates["execution_profile_signature"] = execution_profile_signature
            updates.update(
                _runner_memory_classification_update(
                    descriptor,
                    memory_class=memory_class,
                    source=memory_estimate_source,
                    confidence=memory_estimate_confidence,
                )
            )
            updated = descriptor.model_copy(update=updates)
            self._descriptors[reservation.runner_id] = updated
        self._registry.register(job_id, reservation.runner_id)
        self._notify_state_change("runner_submission_committed")
        return updated

    def confirm_runner_memory_released(self, token: str) -> RunnerDescriptor | None:
        with self._lock:
            reservation = self._reservations.pop(token, None)
            if reservation is None or reservation.kind is not RunnerReservationKind.EVICTION:
                return None
            descriptor = self._descriptor_locked(reservation.runner_id)
            updated = descriptor.model_copy(
                update={
                    "status": (
                        RunnerStatus.IDLE
                        if descriptor.kind is RunnerKind.CORE_COMFYUI
                        else RunnerStatus.STOPPED
                    ),
                    "last_workflow_id": None,
                    "model_residency_signature": None,
                    "model_residency_payload": {},
                    "execution_profile_signature": None,
                    "observed_idle_vram_mb": None,
                    "observed_idle_ram_mb": None,
                    # Residency is cleared, so stale execution-peak observations
                    # must not keep being read as current loaded-model memory by
                    # the resident-memory helpers, and the runner is no longer
                    # classified by the workload it just released.
                    "observed_execution_peak_vram_mb": None,
                    "observed_execution_peak_ram_mb": None,
                    "memory_class": RunnerMemoryClass.UNKNOWN,
                    "memory_estimate_source": RunnerMemoryEstimateSource.UNKNOWN,
                    "memory_estimate_confidence": RunnerMemoryEstimateConfidence.UNKNOWN,
                    # The released runner no longer holds anything a closed-view
                    # cooldown should protect or re-release.
                    "closed_view_cooldown_expires_at": None,
                    "last_used_at": _iso(self._now()),
                    "reservation_token": None,
                    "reservation_kind": None,
                }
            )
            self._descriptors[reservation.runner_id] = updated
        self._notify_state_change("runner_memory_released")
        return updated

    def _reserve_runner(
        self,
        runner_id: str,
        *,
        kind: RunnerReservationKind,
        allowed_statuses: set[RunnerStatus],
        reserved_status: RunnerStatus,
        workflow_id: str | None = None,
    ) -> RunnerReservation | None:
        with self._lock:
            if (
                kind is RunnerReservationKind.SUBMISSION
                and self._runtime_activation_in_progress
            ):
                return None
            descriptor = self._descriptor_locked(runner_id)
            if descriptor.reservation_token is not None or descriptor.current_job_id is not None:
                return None
            if descriptor.status not in allowed_statuses:
                return None
            reservation = RunnerReservation(
                token=f"runner-reservation-{uuid.uuid4().hex}",
                runner_id=runner_id,
                kind=kind,
                prior_status=descriptor.status,
                workflow_id=workflow_id,
                created_at=_iso(self._now()),
            )
            self._reservations[reservation.token] = reservation
            self._descriptors[runner_id] = descriptor.model_copy(
                update={
                    "status": reserved_status,
                    "reservation_token": reservation.token,
                    "reservation_kind": reservation.kind,
                }
            )
            return reservation

    def _transition_reserved_runner(self, token: str, status: RunnerStatus) -> RunnerDescriptor | None:
        with self._lock:
            reservation = self._reservations.get(token)
            if reservation is None:
                return None
            descriptor = self._descriptor_locked(reservation.runner_id)
            updated = descriptor.model_copy(update={"status": status})
            self._descriptors[reservation.runner_id] = updated
            return updated

    def _descriptor_locked(self, runner_id: str) -> RunnerDescriptor:
        descriptor = self._descriptors.get(runner_id)
        if descriptor is None:
            raise RunnerNotFoundError(f"Unknown runner: {runner_id}")
        return descriptor

    def bind_workflow_runner(self, workflow_id: str, runner_id: str) -> RunnerDescriptor:
        descriptor = self.get_runner(runner_id)
        with self._lock:
            self._workflow_runners[workflow_id] = runner_id
        return descriptor

    def unbind_workflow_runner(self, workflow_id: str) -> None:
        with self._lock:
            self._workflow_runners.pop(workflow_id, None)

    def unbind_runner(self, runner_id: str) -> None:
        with self._lock:
            self._workflow_runners = {
                workflow_id: bound_runner_id
                for workflow_id, bound_runner_id in self._workflow_runners.items()
                if bound_runner_id != runner_id
            }
            lease_ids = [
                lease_id
                for lease_id, lease in self._workflow_leases.items()
                if lease.runner_id == runner_id
            ]
            for lease_id in lease_ids:
                self._workflow_leases.pop(lease_id, None)

    # ------------------------------------------------------------------
    # Job routing
    # ------------------------------------------------------------------

    def register_job(self, job_id: str, runner_id: str) -> None:
        # Force a lookup so unknown runners surface immediately.
        self.get_runner(runner_id)
        self._registry.register(job_id, runner_id)

    def runner_for_job(self, job_id: str) -> RunnerDescriptor:
        runner_id = self._registry.runner_for(job_id)
        if runner_id is None:
            raise JobRunnerNotFoundError(f"No runner registered for job: {job_id}")
        return self.get_runner(runner_id)

    def adapter_for_job(self, job_id: str) -> EngineAdapter:
        descriptor = self.runner_for_job(job_id)
        return self.get_adapter(descriptor.runner_id)

    def forget_job(self, job_id: str) -> None:
        self._registry.unregister(job_id)

    def acquire_output_stream_lease(self, runner_id: str) -> None:
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            self._descriptors[runner_id] = descriptor.model_copy(
                update={"output_stream_lease_count": descriptor.output_stream_lease_count + 1}
            )

    def release_output_stream_lease(self, runner_id: str) -> None:
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            self._descriptors[runner_id] = descriptor.model_copy(
                update={"output_stream_lease_count": max(0, descriptor.output_stream_lease_count - 1)}
            )

    # ------------------------------------------------------------------
    # Phase 5f lifecycle policy
    # ------------------------------------------------------------------

    def runner_selection_for(
        self,
        *,
        runner_process_compatibility_key: str,
        memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN,
    ) -> RunnerSelectionDecision:
        """Decide whether a requested workflow should reuse, switch, or queue.

        This does not start or stop processes. It is the policy decision the
        process coordinator can act on while the job registry keeps progress,
        cancellation, and result lookup routed by job id.
        """
        requested_memory = _effective_memory_class(memory_class)
        with self._lock:
            runners = list(self._descriptors.values())

        compatible = [
            runner
            for runner in runners
            if runner.runner_process_compatibility_key == runner_process_compatibility_key
            and _runner_is_resident(runner)
        ]
        if compatible:
            runner = _preferred_runner(compatible)
            return RunnerSelectionDecision(
                action=RunnerSelectionAction.REUSE,
                runner_id=runner.runner_id,
                reason="compatible_runner_resident",
            )

        if requested_memory is not RunnerMemoryClass.GPU_HEAVY:
            return RunnerSelectionDecision(
                action=RunnerSelectionAction.START_NEW,
                reason="no_compatible_runner",
            )

        incompatible_gpu = [
            runner
            for runner in runners
            if _runner_is_resident(runner)
            and runner.kind is RunnerKind.ISOLATED_COMFYUI
            and _effective_memory_class(runner.memory_class) is RunnerMemoryClass.GPU_HEAVY
        ]
        running = [
            runner
            for runner in incompatible_gpu
            if runner.status is RunnerStatus.RUNNING or runner.current_job_id or runner.output_stream_lease_count
        ]
        if running:
            runner = _preferred_runner(running)
            return RunnerSelectionDecision(
                action=RunnerSelectionAction.QUEUE_PENDING_SWITCH,
                queued_behind_runner_id=runner.runner_id,
                reason="incompatible_gpu_runner_running",
            )

        if incompatible_gpu:
            runner = _preferred_runner(incompatible_gpu)
            return RunnerSelectionDecision(
                action=RunnerSelectionAction.SWITCH,
                evict_runner_id=runner.runner_id,
                reason="evict_idle_incompatible_gpu_runner",
            )

        return RunnerSelectionDecision(
            action=RunnerSelectionAction.START_NEW,
            reason="no_resident_gpu_runner",
        )

    def mark_runner_job_started(
        self,
        runner_id: str,
        job_id: str,
        *,
        workflow_id: str | None = None,
    ) -> RunnerDescriptor:
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            updated = descriptor.model_copy(
                update={
                    "status": RunnerStatus.RUNNING,
                    "current_job_id": job_id,
                    "current_workflow_id": workflow_id,
                    "last_workflow_id": workflow_id or descriptor.last_workflow_id,
                    "last_used_at": _iso(self._now()),
                }
            )
            self._descriptors[runner_id] = updated
        self._notify_state_change("runner_job_started")
        return updated

    def mark_runner_job_finished(self, runner_id: str, job_id: str | None = None) -> RunnerDescriptor:
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            if job_id is not None and descriptor.current_job_id not in {None, job_id}:
                return descriptor
            next_status = RunnerStatus.IDLE_WARM if descriptor.open_workflow_lease_count > 0 else RunnerStatus.IDLE
            updated = descriptor.model_copy(
                update={
                    "status": next_status,
                    "current_job_id": None,
                    "current_workflow_id": None,
                    "last_used_at": _iso(self._now()),
                }
            )
            self._descriptors[runner_id] = updated
        self._notify_state_change("runner_job_finished")
        return updated

    def mark_runner_memory_released(self, runner_id: str) -> RunnerDescriptor:
        """Clear warm-workflow state after an idle runner unloads its model cache."""
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            updated = descriptor.model_copy(
                update={
                    "status": RunnerStatus.IDLE,
                    "last_workflow_id": None,
                    "model_residency_signature": None,
                    "model_residency_payload": {},
                    "execution_profile_signature": None,
                    "observed_idle_vram_mb": None,
                    "observed_idle_ram_mb": None,
                    "last_used_at": _iso(self._now()),
                }
            )
            self._descriptors[runner_id] = updated
        self._notify_state_change("runner_memory_released")
        return updated

    def open_workflow_lease(
        self,
        workflow_id: str,
        runner_id: str,
        *,
        lease_id: str | None = None,
    ) -> str:
        lease_id = lease_id or f"lease-{uuid.uuid4().hex}"
        with self._lock:
            descriptor = self._descriptor_locked(runner_id)
            now = self._now()
            self._workflow_leases[lease_id] = WorkflowLeaseRecord(
                workflow_id=workflow_id,
                runner_id=runner_id,
                opened_at=now,
                last_heartbeat_at=now,
            )
            lease_ids = self._workflow_lease_ids_for_runner_locked(runner_id)
            status = (
                RunnerStatus.IDLE_WARM
                if descriptor.status in {RunnerStatus.READY, RunnerStatus.IDLE, RunnerStatus.IDLE_WARM}
                else descriptor.status
            )
            updated = descriptor.model_copy(
                update={
                    "status": status,
                    "open_workflow_lease_count": len(lease_ids),
                    "open_workflow_lease_ids": lease_ids,
                    "closed_view_cooldown_expires_at": None,
                    "last_used_at": _iso(now),
                }
            )
            self._descriptors[runner_id] = updated
        return lease_id

    def heartbeat_workflow_lease(
        self,
        lease_id: str,
        *,
        workflow_id: str | None = None,
    ) -> RunnerDescriptor | None:
        with self._lock:
            lease = self._workflow_leases.get(lease_id)
            if lease is None:
                return None
            if workflow_id is not None and lease.workflow_id != workflow_id:
                return None
            descriptor = self._descriptors.get(lease.runner_id)
            if descriptor is None:
                return None
            self._workflow_leases[lease_id] = lease.model_copy(
                update={"last_heartbeat_at": self._now()}
            )
            return descriptor

    def close_workflow_lease(
        self,
        lease_id: str,
        *,
        workflow_id: str | None = None,
    ) -> RunnerDescriptor | None:
        with self._lock:
            lease = self._workflow_leases.get(lease_id)
            if lease is None:
                return None
            if workflow_id is not None and lease.workflow_id != workflow_id:
                return None
            return self._close_workflow_lease_locked(lease_id, lease)

    def expire_stale_workflow_leases(
        self,
        ttl_seconds: float,
    ) -> list[ExpiredWorkflowLease]:
        with self._lock:
            cutoff = self._now() - timedelta(seconds=max(0.0, ttl_seconds))
            stale = [
                (lease_id, lease)
                for lease_id, lease in self._workflow_leases.items()
                if lease.last_heartbeat_at < cutoff
            ]
            expired: list[ExpiredWorkflowLease] = []
            for lease_id, lease in stale:
                updated = self._close_workflow_lease_locked(lease_id, lease)
                if updated is not None:
                    expired.append(
                        ExpiredWorkflowLease(
                            lease_id=lease_id,
                            workflow_id=lease.workflow_id,
                            runner=updated,
                        )
                    )
            return expired

    def has_workflow_leases(self) -> bool:
        with self._lock:
            return bool(self._workflow_leases)

    def _close_workflow_lease_locked(
        self,
        lease_id: str,
        lease: WorkflowLeaseRecord,
    ) -> RunnerDescriptor | None:
        self._workflow_leases.pop(lease_id, None)
        descriptor = self._descriptors.get(lease.runner_id)
        if descriptor is None:
            return None
        now = self._now()
        lease_ids = self._workflow_lease_ids_for_runner_locked(lease.runner_id)
        expires_at = None
        status = descriptor.status
        if not lease_ids:
            expires_at = _iso(now + timedelta(seconds=self.closed_view_cooldown_seconds))
            if status is RunnerStatus.IDLE_WARM:
                status = RunnerStatus.IDLE
        updated = descriptor.model_copy(
            update={
                "status": status,
                "open_workflow_lease_count": len(lease_ids),
                "open_workflow_lease_ids": lease_ids,
                "closed_view_cooldown_expires_at": expires_at,
                "last_used_at": _iso(now),
            }
        )
        self._descriptors[lease.runner_id] = updated
        return updated

    def _workflow_lease_ids_for_runner_locked(self, runner_id: str) -> list[str]:
        return sorted(
            lease_id
            for lease_id, lease in self._workflow_leases.items()
            if lease.runner_id == runner_id
        )

    def enqueue_runner_start(
        self,
        *,
        workflow_id: str,
        kind: QueuedRunnerStartKind,
        queued_behind_runner_id: str | None = None,
        reason: str | None = None,
        queue_id: str | None = None,
    ) -> QueuedRunnerStart:
        if queued_behind_runner_id is not None:
            self.get_runner(queued_behind_runner_id)
        queued = QueuedRunnerStart(
            queue_id=queue_id or f"runner-queue-{uuid.uuid4().hex}",
            workflow_id=workflow_id,
            kind=kind,
            queued_behind_runner_id=queued_behind_runner_id,
            reason=reason,
            created_at=_iso(self._now()),
            updated_at=_iso(self._now()),
        )
        with self._lock:
            self._queued_runner_starts[queued.queue_id] = queued
        self._notify_state_change("runner_start_queued")
        return queued

    def list_queued_runner_starts(
        self,
        *,
        status: QueuedRunnerStartStatus | None = QueuedRunnerStartStatus.QUEUED,
    ) -> list[QueuedRunnerStart]:
        with self._lock:
            queued = list(self._queued_runner_starts.values())
        if status is not None:
            queued = [item for item in queued if item.status is status]
        return sorted(queued, key=lambda item: (item.created_at, item.queue_id))

    def get_queued_runner_start(self, queue_id: str) -> QueuedRunnerStart | None:
        with self._lock:
            return self._queued_runner_starts.get(queue_id)

    def queued_runner_start_for_workflow(self, workflow_id: str) -> QueuedRunnerStart | None:
        with self._lock:
            queued = [
                item
                for item in self._queued_runner_starts.values()
                if item.workflow_id == workflow_id
                and item.status
                in {
                    QueuedRunnerStartStatus.QUEUED,
                    QueuedRunnerStartStatus.HANDING_OFF,
                    QueuedRunnerStartStatus.REQUEUED,
                }
            ]
        return sorted(queued, key=lambda item: (item.created_at, item.queue_id))[0] if queued else None

    def cancel_queued_runner_start(self, queue_id: str) -> QueuedRunnerStart | None:
        with self._lock:
            queued = self._queued_runner_starts.get(queue_id)
            if queued is None:
                return None
            if queued.status is QueuedRunnerStartStatus.CANCELED:
                return queued
            updated = queued.model_copy(
                update={
                    "status": QueuedRunnerStartStatus.CANCELED,
                    "canceled_at": _iso(self._now()),
                    "cancel_requested": True,
                }
            )
            self._queued_runner_starts[queue_id] = updated
        self._notify_state_change("runner_start_canceled")
        return updated

    def pop_next_queued_runner_start(
        self,
        *,
        released_runner_id: str | None = None,
    ) -> QueuedRunnerStart | None:
        with self._lock:
            queued = sorted(
                (
                    item
                    for item in self._queued_runner_starts.values()
                    if item.status is QueuedRunnerStartStatus.QUEUED
                    and (released_runner_id is None or item.queued_behind_runner_id in {None, released_runner_id})
                ),
                key=lambda item: (item.created_at, item.queue_id),
            )
            if not queued:
                return None
            selected = queued[0]
            self._queued_runner_starts.pop(selected.queue_id, None)
            return selected

    def claim_next_queued_runner_start(
        self,
        *,
        released_runner_id: str | None = None,
    ) -> QueuedRunnerStart | None:
        with self._lock:
            queued = sorted(
                (
                    item
                    for item in self._queued_runner_starts.values()
                    if item.status in {QueuedRunnerStartStatus.QUEUED, QueuedRunnerStartStatus.REQUEUED}
                    and (released_runner_id is None or item.queued_behind_runner_id in {None, released_runner_id})
                ),
                key=lambda item: (item.created_at, item.queue_id),
            )
            if not queued:
                return None
            selected = queued[0]
            updated = selected.model_copy(
                update={
                    "status": QueuedRunnerStartStatus.HANDING_OFF,
                    "attempt_count": selected.attempt_count + 1,
                    "updated_at": _iso(self._now()),
                }
            )
            self._queued_runner_starts[selected.queue_id] = updated
            return updated

    def finish_queued_runner_start(
        self,
        queue_id: str,
        *,
        status: QueuedRunnerStartStatus,
        reason: str | None = None,
    ) -> QueuedRunnerStart | None:
        with self._lock:
            queued = self._queued_runner_starts.get(queue_id)
            if queued is None:
                return None
            updated = queued.model_copy(
                update={"status": status, "last_reason": reason, "updated_at": _iso(self._now())}
            )
            if status is QueuedRunnerStartStatus.SUBMITTED:
                self._queued_runner_starts.pop(queue_id, None)
            else:
                self._queued_runner_starts[queue_id] = updated
        if status is not QueuedRunnerStartStatus.REQUEUED:
            self._notify_state_change("runner_start_transitioned")
        return updated

    @staticmethod
    def _workflow_id(workflow_package: object) -> str | None:
        metadata = getattr(workflow_package, "metadata", None)
        workflow_id = getattr(metadata, "id", None)
        return workflow_id if isinstance(workflow_id, str) else None

    def _notify_state_change(self, reason: str) -> None:
        if self._state_change_notifier is not None:
            self._state_change_notifier(reason)

    def _configure_adapter_terminal_notifier(self, adapter: EngineAdapter) -> None:
        configure_terminal_notifier = getattr(adapter, "configure_terminal_notifier", None)
        if callable(configure_terminal_notifier):
            configure_terminal_notifier(self._terminal_notifier)


def _effective_memory_class(memory_class: RunnerMemoryClass) -> RunnerMemoryClass:
    # Until the Memory Governor can prove a medium runner has enough margin to
    # co-reside, the conservative fallback treats medium/unknown as heavy.
    if memory_class in {RunnerMemoryClass.UNKNOWN, RunnerMemoryClass.GPU_MEDIUM}:
        return RunnerMemoryClass.GPU_HEAVY
    return memory_class


_MEMORY_ESTIMATE_SOURCE_RANK = {
    RunnerMemoryEstimateSource.LOCAL_OBSERVED: 4,
    RunnerMemoryEstimateSource.CREATOR_OBSERVED: 3,
    RunnerMemoryEstimateSource.DECLARED: 2,
    RunnerMemoryEstimateSource.HEURISTIC: 1,
    RunnerMemoryEstimateSource.UNKNOWN: 0,
}

# Raw class strength (heavier is stronger). Used to keep same-source evidence
# from downgrading a runner to a lighter class. Note this is the raw class, not
# `_effective_memory_class`, so MEDIUM and HEAVY remain distinguishable.
_MEMORY_CLASS_STRENGTH = {
    RunnerMemoryClass.GPU_HEAVY: 4,
    RunnerMemoryClass.GPU_MEDIUM: 3,
    RunnerMemoryClass.GPU_LIGHT: 2,
    RunnerMemoryClass.CPU_ONLY: 1,
    RunnerMemoryClass.UNKNOWN: 0,
}


def _runner_memory_classification_update(
    descriptor: RunnerDescriptor,
    *,
    memory_class: RunnerMemoryClass | None,
    source: RunnerMemoryEstimateSource | None,
    confidence: RunnerMemoryEstimateConfidence | None,
) -> dict[str, object]:
    """Return descriptor updates that set the runner's memory classification
    without downgrading stronger evidence with weaker evidence.

    Rules:

    * UNKNOWN is replaced by any useful class, but a useful class is never
      replaced by UNKNOWN.
    * Stronger source evidence (local-observed > creator-observed > declared >
      heuristic > unknown) replaces weaker source evidence.
    * Weaker source evidence never overwrites stronger source evidence.
    * Equal-source evidence may update confidence and a same/heavier class, but
      must not downgrade the class to a lighter one (e.g. local-observed heavy
      stays heavy when a later smaller run is observed). The supported way to
      legitimately re-classify lighter is to clear residency first, which resets
      the class to UNKNOWN (see `confirm_runner_memory_released`).
    """
    if memory_class is None or source is None:
        return {}
    # Never replace a useful class with UNKNOWN (unknown is replaced by useful
    # estimates, not the other way around).
    if (
        memory_class is RunnerMemoryClass.UNKNOWN
        and descriptor.memory_class is not RunnerMemoryClass.UNKNOWN
    ):
        return {}
    incoming_rank = _MEMORY_ESTIMATE_SOURCE_RANK.get(source, 0)
    existing_rank = _MEMORY_ESTIMATE_SOURCE_RANK.get(descriptor.memory_estimate_source, 0)
    if incoming_rank < existing_rank:
        return {}
    if (
        incoming_rank == existing_rank
        and descriptor.memory_class is not RunnerMemoryClass.UNKNOWN
        and _MEMORY_CLASS_STRENGTH.get(memory_class, 0)
        < _MEMORY_CLASS_STRENGTH.get(descriptor.memory_class, 0)
    ):
        # Same-strength evidence must not downgrade to a lighter class.
        return {}
    return {
        "memory_class": memory_class,
        "memory_estimate_source": source,
        "memory_estimate_confidence": (
            confidence
            if confidence is not None
            else descriptor.memory_estimate_confidence
        ),
    }


def _runner_is_resident(runner: RunnerDescriptor) -> bool:
    return runner.status in {
        RunnerStatus.READY,
        RunnerStatus.IDLE,
        RunnerStatus.IDLE_WARM,
        RunnerStatus.RESERVING,
        RunnerStatus.SUBMITTING,
        RunnerStatus.RUNNING,
        RunnerStatus.QUEUED,
        RunnerStatus.QUEUED_PENDING_SWITCH,
        RunnerStatus.QUEUED_PENDING_MEMORY,
        RunnerStatus.LOADING_MODEL,
        RunnerStatus.CO_RESIDENT,
    }


def _preferred_runner(runners: list[RunnerDescriptor]) -> RunnerDescriptor:
    return sorted(runners, key=lambda runner: (runner.last_used_at or "", runner.runner_id), reverse=True)[0]


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
