import type {
  EngineJob,
  JobProgress,
  JobResult,
} from "../../lib/api/noofyApi";
import type { WorkflowRuntimeHandleSource, WorkflowTabRuntimeState } from "../app/WorkflowTabs";
import type { WorkflowRunHandleSnapshot } from "../app/sessionRestore";
import type { TrackedRun } from "./workflowRunStateTypes";

export const terminalStatuses = new Set(["completed", "failed", "canceled"]);
export const activeWorkflowProgressStatuses = new Set(["queued", "running", "queued_pending_memory"]);
export const optimisticJobId = "__pending_workflow_run__";

export function isTrackableJob(job: EngineJob) {
  return activeWorkflowProgressStatuses.has(job.status);
}

export function isActiveWorkflowProgress(progress: JobProgress | null | undefined) {
  return Boolean(progress?.status && activeWorkflowProgressStatuses.has(progress.status));
}

export function isTrackedRunActive(run: TrackedRun) {
  return activeWorkflowProgressStatuses.has(run.status);
}

export function cancelableWorkflowRunCount(runs: TrackedRun[]) {
  return runs.filter(isTrackedRunActive).length;
}

export function selectCurrentTrackedRun(runs: TrackedRun[]) {
  const active = runs.filter(isTrackedRunActive);
  return (
    active.find((run) => run.status === "running") ??
    active.find((run) => run.status === "queued") ??
    active.find((run) => run.status === "queued_pending_memory") ??
    active[0] ??
    null
  );
}

export function trackedRunHandle(run: TrackedRun) {
  return run.type === "queue" ? run.queueId : run.jobId;
}

export function trackedRunHandleSource(run: TrackedRun): WorkflowRuntimeHandleSource {
  return run.type === "queue" ? "workflow_run_queue" : "job";
}

export function progressFromTrackedRun(run: TrackedRun | null, knownProgress: JobProgress | null = null): JobProgress | null {
  if (!run || !isTrackedRunActive(run)) return null;
  if (knownProgress && isActiveWorkflowProgress(knownProgress) && progressMatchesTrackedRun(knownProgress, run)) {
    return knownProgress;
  }
  return {
    job_id: trackedRunHandle(run),
    queue_id: run.type === "queue" ? run.queueId : run.queueId ?? null,
    status: run.status as JobProgress["status"],
    value: null,
    max: null,
    current_node: null,
    message: run.message,
  };
}

export function progressMatchesTrackedRun(progress: JobProgress, run: TrackedRun) {
  const handle = trackedRunHandle(run);
  return (
    progress.job_id === handle
    || progress.queue_id === handle
    || (run.type === "job" && run.queueId ? progress.queue_id === run.queueId : false)
    || (run.type === "queue" && run.jobId ? progress.job_id === run.jobId : false)
  );
}

export function trackedRunFromJob(job: EngineJob, existingClientId?: string): TrackedRun {
  const now = Date.now();
  const base = {
    clientId: existingClientId ?? `${job.queue_id ?? job.job_id}-${now}-${Math.random().toString(16).slice(2)}`,
    status: job.status,
    submittedAt: now,
    updatedAt: now,
    lastPolledAt: null,
    message: job.memory_status?.message ?? job.message ?? null,
  };
  if (job.queue_id && job.queue_id === job.job_id) {
    return { ...base, type: "queue", queueId: job.queue_id, jobId: null };
  }
  return { ...base, type: "job", jobId: job.job_id, queueId: job.queue_id ?? null };
}

export function trackedRunFromProgress(run: TrackedRun, progress: JobProgress, lastPolledAt: number | null = run.lastPolledAt): TrackedRun {
  const now = Date.now();
  const queueId = progress.queue_id ?? (run.type === "queue" ? run.queueId : run.queueId ?? null);
  const base = {
    ...run,
    status: progress.status,
    updatedAt: now,
    lastPolledAt,
    message: progress.message,
  };
  if (queueId && progress.job_id !== queueId) {
    return { ...base, type: "job", jobId: progress.job_id, queueId };
  }
  if (run.type === "queue") {
    return { ...base, type: "queue", queueId: queueId ?? run.queueId, jobId: run.jobId ?? null };
  }
  return { ...base, type: "job", jobId: progress.job_id, queueId };
}

export function trackedRunFromResult(result: JobResult): TrackedRun {
  const now = Date.now();
  const base = {
    clientId: `${result.queue_id ?? result.job_id}-${now}-${Math.random().toString(16).slice(2)}`,
    status: result.status,
    submittedAt: now,
    updatedAt: now,
    lastPolledAt: null,
    message: result.user_message ?? result.error ?? null,
  };
  if (result.queue_id && result.queue_id === result.job_id) {
    return { ...base, type: "queue", queueId: result.queue_id, jobId: null };
  }
  return { ...base, type: "job", jobId: result.job_id, queueId: result.queue_id ?? null };
}

export function trackedRunWithStatus(run: TrackedRun, status: string, message: string | null | undefined): TrackedRun {
  return {
    ...run,
    status,
    message: message ?? run.message,
    updatedAt: Date.now(),
  };
}

export function isQueueOnlyTerminal(run: TrackedRun, progress: JobProgress) {
  return run.type === "queue" && progress.queue_id === run.queueId && progress.job_id === run.queueId;
}

export function workflowCancelProgress(workflowId: string): JobProgress {
  return {
    job_id: `workflow-cancel-${workflowId}`,
    status: "canceled",
    value: null,
    max: null,
    current_node: null,
    message: "Workflow runs canceled.",
  };
}

export function progressFromWorkflowRuntime(runtime: WorkflowTabRuntimeState | null): JobProgress | null {
  const status = runtime?.activeJobStatus;
  const jobId = runtime?.activeJobId ?? runtime?.queueId ?? runtime?.activeJobProgress?.job_id;
  if (!status || !jobId || !activeWorkflowProgressStatuses.has(status)) return null;
  return runtime.activeJobProgress ?? {
    job_id: jobId,
    status: status as JobProgress["status"],
    value: null,
    max: null,
    current_node: null,
    message: "Starting this workflow...",
  };
}

export function terminalProgressFromWorkflowRuntime(runtime: WorkflowTabRuntimeState | null): JobProgress | null {
  const progress = runtime?.activeJobProgress;
  const status = runtime?.activeJobStatus ?? progress?.status;
  if (
    !progress?.job_id
    || (status !== "completed" && status !== "failed" && status !== "canceled")
  ) return null;
  return progress.status === status ? progress : { ...progress, status };
}

export function terminalProgressFromStoredRunHandle(snapshot: WorkflowRunHandleSnapshot | null): JobProgress | null {
  if (!snapshot || !terminalStatuses.has(snapshot.status)) return null;
  return {
    job_id: snapshot.jobId,
    queue_id: snapshot.queueId,
    status: snapshot.status as JobProgress["status"],
    value: snapshot.status === "completed" ? 1 : null,
    max: snapshot.status === "completed" ? 1 : null,
    current_node: null,
    message: snapshot.status === "completed" ? "Result saved by the local workflow." : null,
  };
}

export function optimisticProgress(): JobProgress {
  return {
    job_id: optimisticJobId,
    status: "queued",
    value: 0,
    max: null,
    current_node: null,
    message: "Starting this workflow...",
  };
}

export function progressFromSubmittedJob(job: EngineJob): JobProgress {
  return {
    job_id: job.job_id,
    queue_id: job.queue_id ?? null,
    status: job.status,
    value: null,
    max: null,
    current_node: null,
    message: job.memory_status?.message ?? job.message ?? "Starting this workflow...",
    error_code: job.error_code,
    memory_requirement: job.memory_requirement,
    memory_status: job.memory_status,
    developer_details: job.memory_decision ? { memory_decision: job.memory_decision } : {},
  };
}

export function workflowHandleSource(job: EngineJob): WorkflowRuntimeHandleSource {
  if (job.status === "queued_pending_memory" && job.engine === "noofy") {
    return "workflow_run_queue";
  }
  return "job";
}

export function runnerIdFromLease(runner: Record<string, unknown> | null) {
  const runnerId = runner?.runner_id;
  return typeof runnerId === "string" ? runnerId : null;
}

export function terminalProgressFromResult(result: JobResult): JobProgress {
  return {
    job_id: result.job_id,
    queue_id: result.queue_id ?? null,
    status: result.status,
    value: null,
    max: null,
    current_node: null,
    message: terminalResultProgressMessage(result),
    error_code: result.error_code,
    memory_requirement: result.memory_requirement,
    developer_details: result.developer_details,
  };
}

export function progressFromRecoveredResult(result: JobResult): JobProgress {
  return {
    job_id: result.job_id,
    queue_id: result.queue_id ?? null,
    status: result.status,
    value: null,
    max: null,
    current_node: null,
    message: result.user_message ?? result.error ?? null,
    error_code: result.error_code,
    memory_requirement: result.memory_requirement,
    developer_details: result.developer_details,
  };
}

function terminalResultProgressMessage(result: JobResult): string | null {
  if (result.user_message) return result.user_message;
  if (result.error) return result.error;
  if (result.status === "completed") return "Execution completed";
  if (result.status === "canceled") return "Run canceled.";
  return null;
}
