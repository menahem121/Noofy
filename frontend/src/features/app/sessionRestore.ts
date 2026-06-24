const SESSION_RESTART_STORAGE_KEY = "noofy.sessionRestart.v1";
const ACTIVE_RUN_WORKFLOWS_STORAGE_KEY = "noofy.activeRunWorkflows.v1";
const WORKFLOW_RUN_HANDLES_STORAGE_KEY = "noofy.workflowRunHandles.v1";
const RESTART_MARKER_MAX_AGE_MS = 10 * 60 * 1000;
const RUN_HANDLE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export const APP_RESTARTED_RUN_MESSAGE = "The app restarted. Run this workflow again when ready.";
export const VANISHED_RUN_MESSAGE = "This run is no longer active. Run this workflow again when ready.";

interface SessionRestartMarker {
  backendSessionId: string;
  detectedAt: number;
}

interface ActiveRunWorkflowsMarker {
  workflowIds: string[];
  updatedAt: number;
}

export interface WorkflowRunHandleSnapshot {
  workflowId: string;
  jobId: string;
  queueId: string | null;
  status: string;
  updatedAt: number;
}

interface WorkflowRunHandlesMarker {
  handles: Record<string, WorkflowRunHandleSnapshot>;
}

export function recordBackendSessionRestart(backendSessionId: string) {
  try {
    window.sessionStorage.setItem(
      SESSION_RESTART_STORAGE_KEY,
      JSON.stringify({ backendSessionId, detectedAt: Date.now() } satisfies SessionRestartMarker),
    );
  } catch {
    // Runtime restart handling remains best effort when storage is unavailable.
  }
}

export function storeActiveRunWorkflowIds(workflowIds: string[]) {
  try {
    if (workflowIds.length === 0) {
      window.sessionStorage.removeItem(ACTIVE_RUN_WORKFLOWS_STORAGE_KEY);
      return;
    }
    window.sessionStorage.setItem(
      ACTIVE_RUN_WORKFLOWS_STORAGE_KEY,
      JSON.stringify({ workflowIds, updatedAt: Date.now() } satisfies ActiveRunWorkflowsMarker),
    );
  } catch {
    // Restart notices are a convenience; runtime truth remains backend-owned.
  }
}

export function storeWorkflowRunHandle(
  workflowId: string,
  snapshot: Omit<WorkflowRunHandleSnapshot, "workflowId" | "updatedAt"> & { updatedAt?: number },
) {
  const normalizedWorkflowId = workflowId.trim();
  const jobId = snapshot.jobId.trim();
  if (!normalizedWorkflowId || !jobId || !snapshot.status.trim()) return;
  try {
    const marker = loadWorkflowRunHandlesMarker();
    marker.handles[normalizedWorkflowId] = {
      workflowId: normalizedWorkflowId,
      jobId,
      queueId: snapshot.queueId?.trim() || null,
      status: snapshot.status.trim(),
      updatedAt: snapshot.updatedAt ?? Date.now(),
    };
    window.sessionStorage.setItem(WORKFLOW_RUN_HANDLES_STORAGE_KEY, JSON.stringify(marker));
  } catch {
    // Result recovery is best effort; the backend remains authoritative.
  }
}

export function loadWorkflowRunHandle(workflowId: string): WorkflowRunHandleSnapshot | null {
  try {
    const marker = loadWorkflowRunHandlesMarker();
    const snapshot = marker.handles[workflowId];
    if (!isRecentWorkflowRunHandle(snapshot)) return null;
    return snapshot;
  } catch {
    return null;
  }
}

export function clearWorkflowRunHandle(workflowId: string) {
  try {
    const marker = loadWorkflowRunHandlesMarker();
    if (!(workflowId in marker.handles)) return;
    delete marker.handles[workflowId];
    if (Object.keys(marker.handles).length === 0) {
      window.sessionStorage.removeItem(WORKFLOW_RUN_HANDLES_STORAGE_KEY);
      return;
    }
    window.sessionStorage.setItem(WORKFLOW_RUN_HANDLES_STORAGE_KEY, JSON.stringify(marker));
  } catch {
    // Storage cleanup should not affect workflow navigation.
  }
}

export function loadRestartRecoveryNotices(): Record<string, string> {
  if (!hasRecentBackendSessionRestart()) return {};
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_RUN_WORKFLOWS_STORAGE_KEY);
    if (!raw) return {};
    const marker = JSON.parse(raw) as Partial<ActiveRunWorkflowsMarker>;
    if (
      !Array.isArray(marker.workflowIds)
      || typeof marker.updatedAt !== "number"
      || Date.now() - marker.updatedAt > RESTART_MARKER_MAX_AGE_MS
    ) {
      return {};
    }
    return Object.fromEntries(
      marker.workflowIds
        .filter((workflowId): workflowId is string => typeof workflowId === "string" && Boolean(workflowId.trim()))
        .map((workflowId) => [workflowId, APP_RESTARTED_RUN_MESSAGE]),
    );
  } catch {
    return {};
  }
}

export function vanishedRunRecoveryMessage() {
  return hasRecentBackendSessionRestart() ? APP_RESTARTED_RUN_MESSAGE : VANISHED_RUN_MESSAGE;
}

export function clearBackendSessionRestartMarker() {
  try {
    window.sessionStorage.removeItem(SESSION_RESTART_STORAGE_KEY);
  } catch {
    // Recovery copy remains valid even when storage cleanup is unavailable.
  }
}

export function hasRecentBackendSessionRestart() {
  try {
    const raw = window.sessionStorage.getItem(SESSION_RESTART_STORAGE_KEY);
    if (!raw) return false;
    const marker = JSON.parse(raw) as Partial<SessionRestartMarker>;
    return (
      typeof marker.backendSessionId === "string"
      && Boolean(marker.backendSessionId.trim())
      && typeof marker.detectedAt === "number"
      && Date.now() - marker.detectedAt <= RESTART_MARKER_MAX_AGE_MS
    );
  } catch {
    return false;
  }
}

function loadWorkflowRunHandlesMarker(): WorkflowRunHandlesMarker {
  const raw = window.sessionStorage.getItem(WORKFLOW_RUN_HANDLES_STORAGE_KEY);
  if (!raw) return { handles: {} };
  const parsed = JSON.parse(raw) as Partial<WorkflowRunHandlesMarker>;
  const handles: Record<string, WorkflowRunHandleSnapshot> = {};
  if (!parsed.handles || typeof parsed.handles !== "object") return { handles };
  for (const [workflowId, value] of Object.entries(parsed.handles)) {
    if (!isRecentWorkflowRunHandle(value)) continue;
    handles[workflowId] = value;
  }
  return { handles };
}

function isRecentWorkflowRunHandle(value: unknown): value is WorkflowRunHandleSnapshot {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<WorkflowRunHandleSnapshot>;
  return (
    typeof record.workflowId === "string"
    && Boolean(record.workflowId.trim())
    && typeof record.jobId === "string"
    && Boolean(record.jobId.trim())
    && (record.queueId === null || typeof record.queueId === "string")
    && typeof record.status === "string"
    && Boolean(record.status.trim())
    && typeof record.updatedAt === "number"
    && Date.now() - record.updatedAt <= RUN_HANDLE_MAX_AGE_MS
  );
}
