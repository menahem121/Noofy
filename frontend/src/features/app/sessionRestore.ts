const BACKEND_SESSION_STORAGE_KEY = "noofy.backendSession.v1";
const TAB_BACKEND_SESSION_STORAGE_KEY = "noofy.tabBackendSession.v1";
const SESSION_RESTART_STORAGE_KEY = "noofy.sessionRestart.v1";
const ACTIVE_RUN_WORKFLOWS_STORAGE_KEY = "noofy.activeRunWorkflows.v1";
const RESTART_MARKER_MAX_AGE_MS = 10 * 60 * 1000;

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

export function adoptBackendSessionId(backendSessionId: string) {
  try {
    window.localStorage.setItem(BACKEND_SESSION_STORAGE_KEY, backendSessionId);
  } catch {
    // Session observation must not depend on browser storage availability.
  }
  try {
    window.sessionStorage.setItem(TAB_BACKEND_SESSION_STORAGE_KEY, backendSessionId);
  } catch {
    // Per-tab restart detection is best effort when storage is unavailable.
  }
}

export function loadObservedBackendSessionId() {
  try {
    return window.sessionStorage.getItem(TAB_BACKEND_SESSION_STORAGE_KEY)?.trim() || null;
  } catch {
    return null;
  }
}

export function recordBackendSessionRestart(backendSessionId: string) {
  adoptBackendSessionId(backendSessionId);
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
