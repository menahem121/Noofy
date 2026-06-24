import { CheckCircle2 } from "lucide-react";

import type {
  EngineJob,
  JobProgress,
  JobResult,
  MemoryRequirement,
  MemoryStatus,
  WorkflowStatusResponse,
  WorkflowValidationResult,
} from "../../lib/api/noofyApi";

export interface MemoryStatusDisplay {
  title: string;
  message: string;
}

export const MEMORY_FAILURE_MESSAGE =
  "Your computer does not have enough available RAM or GPU memory for this workflow right now.";

export function MemoryLoadedPill() {
  return (
    <div
      className="memory-loaded-pill"
      role="status"
      title="The required models are already loaded, so the next run should start faster."
    >
      <CheckCircle2 size={13} aria-hidden="true" />
      <span>Models loaded</span>
    </div>
  );
}

export function isMemoryFailureCode(code: JobResult["error_code"]) {
  return code === "memory_oom" || code === "insufficient_memory";
}

export function emptyComfyUiFailureLogsMessage(code: JobResult["error_code"]) {
  return code === "insufficient_memory"
    ? "ComfyUI did not run because Noofy stopped this workflow before submission."
    : "No ComfyUI engine logs were returned for this failure.";
}

export function MemoryRequirementSummary({ requirement }: { requirement: MemoryRequirement | null }) {
  if (!requirement) return null;
  const rows = [
    memoryRequirementRow(
      "GPU memory",
      requirement.required_vram_mb,
      requirement.total_vram_mb,
      requirement.available_vram_mb,
      requirement.source,
    ),
    memoryRequirementRow(
      "RAM",
      requirement.required_ram_mb,
      requirement.total_ram_mb,
      requirement.available_ram_mb,
      requirement.source,
    ),
  ].filter((row): row is string => Boolean(row));
  if (rows.length === 0) return null;
  return (
    <section className="workflow-memory-requirement" aria-label="Approximate memory requirement">
      <strong>Approximate memory needed</strong>
      {rows.map((row) => <span key={row}>{row}</span>)}
      {requirement.capacity_exceeded === true ? (
        <p>This workflow needs more memory than this machine has. Closing apps or freeing memory is unlikely to make it run.</p>
      ) : requirement.freeing_memory_may_help === true ? (
        <p>This machine has enough total memory, but not enough is free right now.</p>
      ) : null}
    </section>
  );
}

export function MemoryFailureSteps({ requirement }: { requirement: MemoryRequirement | null }) {
  const freeingMemoryMayHelp = requirement?.freeing_memory_may_help === true;
  return (
    <section className="workflow-memory-failure-steps" aria-label="Ways to use less memory">
      <strong>{freeingMemoryMayHelp ? "Try one of these:" : "To run this workflow:"}</strong>
      <ul>
        {freeingMemoryMayHelp ? <li>Close other apps that may be using memory.</li> : null}
        <li>If available, reduce resolution, batch size, or video length.</li>
        <li>Use a lighter model or workflow.</li>
        {freeingMemoryMayHelp ? <li>Free memory, then try again.</li> : null}
      </ul>
    </section>
  );
}

export function memoryStatusTitle(state: string) {
  return memoryStatusFallback(state).title;
}

export function isWarmReusableMemoryState(state: string) {
  return state === "ready_warm_co_resident" || state === "ready_reusing_runner";
}

export function isSilentQueuedMemoryState(state: string) {
  return state === "preparing_run"
    || state === "starting_engine"
    || state === "waiting_for_gpu"
    || state === "waiting_for_active_workflow"
    || state === "queued_behind_active_run"
    || state === "freeing_previous_models"
    || state === "unloading_previous_workflow"
    || state === "freeing_memory"
    || state === "waiting_for_memory_release"
    || state === "retrying_after_memory_cleanup"
    || state === "monitoring_memory";
}

export function isBlockingMemoryState(state: string) {
  return state === "blocked_by_memory" || state === "memory_cleanup_failed" || state.startsWith("blocked_");
}

export function clampBatchCount(value: number) {
  if (!Number.isFinite(value)) return 1;
  return Math.min(99, Math.max(1, Math.round(value)));
}

export function progressMessage(progress: JobProgress | null, result: JobResult | null, memoryStatus: MemoryStatus | null = null) {
  if (memoryStatus) {
    if (!isSilentQueuedMemoryState(memoryStatus.state)) return memoryStatusDisplay(memoryStatus).message;
    if (progress?.status === "queued_pending_memory") return null;
  }
  if (progress?.status === "running") return progress.message ?? "Running workflow...";
  if (progress?.status === "queued") return progress.message ?? "Starting this workflow...";
  if (progress?.status === "queued_pending_memory") return progress.message ?? "Waiting for enough memory.";
  if (progress?.status === "blocked_by_memory") return progress.message ?? "This workflow does not have enough free memory to start.";
  if (progress?.status === "canceled") return "Run canceled.";
  if (result?.status === "completed") return "Result ready.";
  if (result?.status === "failed") return "ComfyUI could not finish this run.";
  return "Run the workflow to create your first result.";
}

export function memoryStatusDisplay(status: MemoryStatus): MemoryStatusDisplay {
  if (isBlockingMemoryState(status.state)) {
    return {
      title: "Not enough memory to run this workflow",
      message: MEMORY_FAILURE_MESSAGE,
    };
  }
  const fallback = memoryStatusFallback(status.state);
  const backendMessage = typeof status.message === "string" ? status.message.trim() : "";
  return {
    title: fallback.title,
    message: shouldUseMemoryStatusFallbackMessage(status.state, backendMessage) ? fallback.message : backendMessage,
  };
}

export function memoryStatusDeveloperDetails(job: EngineJob | null, progress: JobProgress | null) {
  const memoryStatus = progress?.memory_status ?? job?.memory_status ?? null;
  const memoryDecision = progress?.developer_details?.memory_decision ?? job?.memory_decision ?? null;
  if (!memoryStatus && !memoryDecision) return null;
  const details = {
    job_id: progress?.job_id ?? job?.job_id ?? null,
    queue_id: progress?.queue_id ?? job?.queue_id ?? memoryStatus?.queue_id ?? null,
    status: progress?.status ?? job?.status ?? null,
    memory_status: memoryStatus,
    memory_decision: memoryDecision,
  };
  return JSON.stringify(details, null, 2);
}

export function memoryNoticeClass(status: MemoryStatus) {
  if (status.state === "blocked_by_memory" || status.state === "memory_cleanup_failed" || status.state.startsWith("blocked_")) return "notice--error";
  return "notice--warning";
}

interface RunDisabledReasonInput {
  backendKnownUnreachable: boolean;
  engineKnownUnavailable: boolean;
  installStatus: string | null;
  isBlockedByMemory: boolean;
  isWaitingForMemory: boolean;
  dashboardLoadingReason: string | null;
  memoryStatus: MemoryStatus | null;
  missingModels: Array<{ filename: string }>;
  modelSummaryLoading: boolean;
  modelSummaryReady: boolean | undefined;
  validation: WorkflowValidationResult | null;
  validationLoading: boolean;
  workflowStatus: WorkflowStatusResponse | null;
}

export function workflowRunDisabledReason({
  backendKnownUnreachable,
  engineKnownUnavailable,
  installStatus,
  isBlockedByMemory,
  isWaitingForMemory,
  dashboardLoadingReason,
  memoryStatus,
  missingModels,
  modelSummaryLoading,
  modelSummaryReady,
  validation,
  validationLoading,
  workflowStatus,
}: RunDisabledReasonInput): string {
  if (isBlockedByMemory && memoryStatus) return memoryStatusDisplay(memoryStatus).message;
  if (isBlockedByMemory) return "Noofy cannot safely start this workflow with the memory available right now.";
  if (isWaitingForMemory && memoryStatus) return memoryStatusDisplay(memoryStatus).message;
  if (isWaitingForMemory) return "Noofy is waiting for memory to free up.";
  if (backendKnownUnreachable) return "Noofy is offline.";
  if (engineKnownUnavailable) return "ComfyUI is not ready yet.";
  if (dashboardLoadingReason) return dashboardLoadingReason;
  if (installStatus === "unsupported" || workflowStatus?.can_prepare === false) {
    return "This workflow cannot run on this machine.";
  }
  if (modelSummaryLoading) return "Checking required models...";
  if (missingModels.length > 0) {
    const names = missingModels.slice(0, 2).map((model) => model.filename).join(", ");
    const remaining = missingModels.length > 2 ? ` and ${missingModels.length - 2} more` : "";
    return `Add required model before running: ${names}${remaining}.`;
  }
  if (modelSummaryReady === false) return "This workflow needs required models before it can run.";
  if (validation && !validation.valid) {
    return validation.errors[0] ?? "This workflow needs setup before it can run.";
  }
  if (validationLoading || !workflowStatus || !validation) return "Checking whether this workflow can run...";
  return "This workflow is not ready to run.";
}

function memoryRequirementRow(
  label: string,
  requiredMb: number | null,
  totalMb: number | null,
  availableMb: number | null,
  source: string,
) {
  if (requiredMb == null && totalMb == null && availableMb == null) return null;
  if (source === "memory_governor_decision" && availableMb != null) {
    if (requiredMb != null && totalMb != null) {
      return `${label}: about ${formatMemoryGb(requiredMb)} required; about ${formatMemoryGb(availableMb)} free when checked (${formatMemoryGb(totalMb)} total).`;
    }
    if (requiredMb != null) {
      return `${label}: about ${formatMemoryGb(requiredMb)} required; about ${formatMemoryGb(availableMb)} free when checked.`;
    }
    if (totalMb != null) {
      return `${label}: about ${formatMemoryGb(availableMb)} free when checked (${formatMemoryGb(totalMb)} total).`;
    }
    return `${label}: about ${formatMemoryGb(availableMb)} free when checked.`;
  }
  if (requiredMb != null && totalMb != null) {
    if (source === "runtime_oom") {
      return `${label}: about ${formatMemoryGb(requiredMb)} required; about ${formatMemoryGb(totalMb)} was available to this workflow.`;
    }
    return `${label}: about ${formatMemoryGb(requiredMb)} required; this machine has about ${formatMemoryGb(totalMb)}.`;
  }
  if (requiredMb != null) return `${label}: about ${formatMemoryGb(requiredMb)} required.`;
  return `${label}: this machine has about ${formatMemoryGb(totalMb as number)}.`;
}

function formatMemoryGb(memoryMb: number) {
  const value = memoryMb / 1024;
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} GB`;
}

function memoryStatusFallback(state: string): MemoryStatusDisplay {
  if (state === "waiting_for_gpu") {
    return {
      title: "Waiting for the GPU",
      message: "Noofy will start this run when the GPU is available.",
    };
  }
  if (state === "preparing_run") {
    return {
      title: "Preparing run",
      message: "Noofy is preparing this workflow to run.",
    };
  }
  if (state === "starting_engine") {
    return {
      title: "Starting engine",
      message: "Noofy is starting the local ComfyUI engine before this run.",
    };
  }
  if (state === "queued_behind_active_run") {
    return {
      title: "Run queued",
      message: "This run will start when the current run finishes.",
    };
  }
  if (state === "waiting_for_active_workflow") {
    return {
      title: "Waiting for another run",
      message: "Noofy will start this workflow after the active run finishes.",
    };
  }
  if (state === "freeing_previous_models") {
    return {
      title: "Making room for this workflow",
      message: "Noofy is unloading models from the previous run so this one can start.",
    };
  }
  if (state === "unloading_previous_workflow") {
    return {
      title: "Preparing run",
      message: "Noofy is unloading the previous workflow before starting this one.",
    };
  }
  if (state === "freeing_memory" || state === "waiting_for_memory_release") {
    return {
      title: "Freeing memory",
      message: "Noofy is freeing memory before this run starts.",
    };
  }
  if (state === "retrying_after_memory_cleanup") {
    return {
      title: "Trying again",
      message: "Noofy freed memory and is starting this workflow again.",
    };
  }
  if (state === "memory_cleanup_failed") {
    return {
      title: "Not enough memory was freed",
      message: "Noofy tried to free memory, but there still is not enough available for this workflow.",
    };
  }
  if (state === "blocked_external_pressure") {
    return {
      title: "Other GPU work is using memory",
      message: "Another process is using GPU memory that Noofy cannot reclaim.",
    };
  }
  if (state === "blocked_exceeds_capacity") {
    return {
      title: "Workflow exceeds this machine's memory",
      message: "This workflow appears to need more RAM or VRAM than this machine can safely provide.",
    };
  }
  if (state === "blocked_unattributed_pressure") {
    return {
      title: "Memory is still in use",
      message: "Memory is in use, but Noofy cannot free enough of it automatically.",
    };
  }
  if (state === "blocked_by_memory") {
    return {
      title: "Not enough free memory",
      message: "This workflow does not have enough free RAM or GPU memory to start right now.",
    };
  }
  if (state === "ready_warm_co_resident" || state === "ready_reusing_runner") {
    return {
      title: "Models loaded",
      message: "The required models are already loaded, so the next run should start faster.",
    };
  }
  return {
    title: "Checking memory",
    message: "Noofy is checking available memory before this workflow starts.",
  };
}

function shouldUseMemoryStatusFallbackMessage(state: string, backendMessage: string) {
  if (!backendMessage) return true;
  const normalized = backendMessage.toLowerCase();
  if (state === "blocked_by_memory") return false;
  return (
    normalized === "not enough memory" ||
    normalized.includes("not enough memory is available") ||
    normalized.includes("needs more memory than noofy can safely use")
  );
}
