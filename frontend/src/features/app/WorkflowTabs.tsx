import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";

import {
  closeWorkflowRunnerLeaseKeepalive,
  fetchJobProgress,
  heartbeatWorkflowRunnerLease,
  type JobProgress,
  type JobStatus,
} from "../../lib/api/noofyApi";
import { invalidateWorkflowRunPageCache } from "../workflows/workflowRunPageCache";
import { useOptionalRuntimeStatus } from "./RuntimeStatusProvider";
import {
  APP_RESTARTED_RUN_MESSAGE,
  clearBackendSessionRecoveryStorage,
  clearWorkflowRunHandle,
  hasRecentBackendSessionRestart,
  loadRestartRecoveryWorkflowIds,
  storeWorkflowRunHandle,
  storeActiveRunWorkflowIds,
  vanishedRunRecoveryMessage,
} from "./sessionRestore";

const STORAGE_KEY = "noofy.workflowTabs.v1";
const WORKFLOW_LEASE_HEARTBEAT_INTERVAL_MS = 25_000;
const activeJobStatuses = new Set(["queued", "running", "queued_pending_memory"]);

export type WorkflowRuntimeHandleSource = "job" | "workflow_run_queue" | "runner_start_queue";

export interface WorkflowTab {
  workflowId: string;
  workflowName: string;
  lastActivatedAt: number;
}

export interface WorkflowTabRuntimeState {
  activeJobId: string | null;
  activeJobStatus: JobStatus | string | null;
  activeJobProgress: JobProgress | null;
  activeJobUpdatedAt: number | null;
  handleSource: WorkflowRuntimeHandleSource | null;
  queueId: string | null;
  runnerLeaseId: string | null;
  runnerId: string | null;
}

interface WorkflowTabsContextValue {
  tabs: WorkflowTab[];
  runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>;
  recoveryNoticeByWorkflowId: Record<string, string>;
  openWorkflowTab: (workflowId: string, workflowName?: string) => void;
  closeWorkflowTab: (workflowId: string) => void;
  updateWorkflowTabName: (workflowId: string, workflowName: string) => void;
  setWorkflowRuntime: (workflowId: string, update: Partial<WorkflowTabRuntimeState>) => void;
  clearWorkflowRuntime: (workflowId: string) => void;
  setWorkflowRecoveryNotice: (workflowId: string, message: string) => void;
  dismissWorkflowRecoveryNotice: (workflowId: string) => void;
}

interface WorkflowTabsRouterContextValue {
  activeWorkflowId: string | null;
  onActivateWorkflowTab: (workflowId: string, workflowName?: string) => void;
  onRequestCloseWorkflowTab: (workflowId: string) => void;
}

const emptyRuntimeState: WorkflowTabRuntimeState = {
  activeJobId: null,
  activeJobStatus: null,
  activeJobProgress: null,
  activeJobUpdatedAt: null,
  handleSource: null,
  queueId: null,
  runnerLeaseId: null,
  runnerId: null,
};

const WorkflowTabsContext = createContext<WorkflowTabsContextValue | null>(null);
const WorkflowTabsRouterContext = createContext<WorkflowTabsRouterContextValue | null>(null);

export function WorkflowTabsProvider({ children }: { children: ReactNode }) {
  const runtimeStatus = useOptionalRuntimeStatus();
  const initialRestartRecoveryWorkflowIdsRef = useRef<string[] | null>(null);
  if (initialRestartRecoveryWorkflowIdsRef.current === null) {
    initialRestartRecoveryWorkflowIdsRef.current = loadRestartRecoveryWorkflowIds();
  }
  const [tabs, setTabs] = useState<WorkflowTab[]>(() => loadStoredTabs());
  const initialOpenWorkflowIdsRef = useRef<string[] | null>(null);
  if (initialOpenWorkflowIdsRef.current === null) {
    initialOpenWorkflowIdsRef.current = tabs.map((tab) => tab.workflowId);
  }
  const [runtimeByWorkflowId, setRuntimeByWorkflowId] = useState<Record<string, WorkflowTabRuntimeState>>({});
  const [recoveryNoticeByWorkflowId, setRecoveryNoticeByWorkflowId] = useState<Record<string, string>>(
    () => recoveryNoticesForWorkflowIds(initialRestartRecoveryWorkflowIdsRef.current ?? []),
  );
  const runtimeByWorkflowIdRef = useRef(runtimeByWorkflowId);
  const handledBackendSessionRecoverySeqRef = useRef<number | null>(null);

  useEffect(() => {
    runtimeByWorkflowIdRef.current = runtimeByWorkflowId;
  }, [runtimeByWorkflowId]);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs));
    } catch {
      // Persistent tabs are a convenience; runtime should not depend on them.
    }
  }, [tabs]);

  useEffect(() => {
    storeActiveRunWorkflowIds(
      Object.entries(runtimeByWorkflowId)
        .filter(([, runtime]) => hasActiveRuntimeHandle(runtime))
        .map(([workflowId]) => workflowId),
    );
    for (const [workflowId, runtime] of Object.entries(runtimeByWorkflowId)) {
      const snapshot = workflowRunHandleSnapshotFromRuntime(runtime);
      if (snapshot) storeWorkflowRunHandle(workflowId, snapshot);
    }
  }, [runtimeByWorkflowId]);

  useEffect(() => {
    const recoveredWorkflowIds = initialRestartRecoveryWorkflowIdsRef.current ?? [];
    if (recoveredWorkflowIds.length > 0 || hasRecentBackendSessionRestart()) {
      const cacheWorkflowIds = uniqueWorkflowIds([
        ...(initialOpenWorkflowIdsRef.current ?? []),
        ...recoveredWorkflowIds,
      ]);
      for (const workflowId of cacheWorkflowIds) {
        invalidateWorkflowRunPageCache(workflowId);
      }
      clearBackendSessionRecoveryStorage();
    }
  }, []);

  useEffect(() => {
    const recovery = runtimeStatus?.backendSessionRecovery;
    if (!recovery || handledBackendSessionRecoverySeqRef.current === recovery.sequence) return;
    handledBackendSessionRecoverySeqRef.current = recovery.sequence;

    const affectedWorkflowIds = uniqueWorkflowIds([
      ...loadRestartRecoveryWorkflowIds(),
      ...sessionOwnedWorkflowRuntimeIds(runtimeByWorkflowIdRef.current),
    ]);
    const cacheWorkflowIds = uniqueWorkflowIds([
      ...tabs.map((tab) => tab.workflowId),
      ...affectedWorkflowIds,
    ]);
    clearBackendSessionRecoveryStorage();

    for (const workflowId of cacheWorkflowIds) {
      invalidateWorkflowRunPageCache(workflowId);
    }
    setRuntimeByWorkflowId((current) => removeRuntimeStateForWorkflows(current, cacheWorkflowIds));

    if (affectedWorkflowIds.length > 0) {
      setRecoveryNoticeByWorkflowId((current) => ({
        ...current,
        ...recoveryNoticesForWorkflowIds(affectedWorkflowIds),
      }));
    }

    runtimeStatus.acknowledgeBackendSessionRecovery(recovery.sequence);
  }, [runtimeStatus?.backendSessionRecovery, runtimeStatus?.acknowledgeBackendSessionRecovery, tabs]);

  const openWorkflowTab = useCallback((workflowId: string, workflowName?: string) => {
    const now = Date.now();
    setTabs((current) => {
      const existing = current.find((tab) => tab.workflowId === workflowId);
      const fallbackName = workflowName?.trim() || existing?.workflowName || workflowId;
      if (existing) {
        return current.map((tab) =>
          tab.workflowId === workflowId
            ? { ...tab, workflowName: fallbackName, lastActivatedAt: now }
            : tab,
        );
      }
      return [...current, { workflowId, workflowName: fallbackName, lastActivatedAt: now }];
    });
  }, []);

  const closeWorkflowTab = useCallback((workflowId: string) => {
    setTabs((current) => current.filter((tab) => tab.workflowId !== workflowId));
    clearWorkflowRunHandle(workflowId);
    setRuntimeByWorkflowId((current) => {
      if (!(workflowId in current)) return current;
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
    setRecoveryNoticeByWorkflowId((current) => {
      if (!(workflowId in current)) return current;
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
  }, []);

  const updateWorkflowTabName = useCallback((workflowId: string, workflowName: string) => {
    const normalized = workflowName.trim();
    if (!normalized) return;
    setTabs((current) =>
      current.map((tab) => (tab.workflowId === workflowId ? { ...tab, workflowName: normalized } : tab)),
    );
  }, []);

  const setWorkflowRuntime = useCallback((workflowId: string, update: Partial<WorkflowTabRuntimeState>) => {
    setRuntimeByWorkflowId((current) => {
      const existing = current[workflowId] ?? emptyRuntimeState;
      return {
        ...current,
        [workflowId]: {
          ...existing,
          ...runtimeUpdateWithRetainedNumericProgress(existing, update),
        },
      };
    });
  }, []);

  const clearWorkflowRuntime = useCallback((workflowId: string) => {
    setRuntimeByWorkflowId((current) => {
      if (!(workflowId in current)) return current;
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
  }, []);

  const setWorkflowRecoveryNotice = useCallback((workflowId: string, message: string) => {
    setRecoveryNoticeByWorkflowId((current) => (
      current[workflowId] === message ? current : { ...current, [workflowId]: message }
    ));
  }, []);

  const dismissWorkflowRecoveryNotice = useCallback((workflowId: string) => {
    setRecoveryNoticeByWorkflowId((current) => {
      if (!(workflowId in current)) return current;
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
  }, []);

  useWorkflowRuntimeProgressPolling(runtimeByWorkflowId, setWorkflowRuntime, setWorkflowRecoveryNotice);
  useWorkflowLeaseHeartbeat(runtimeByWorkflowId, setWorkflowRuntime);
  useWorkflowLeasePagehideRelease(runtimeByWorkflowId);

  const value = useMemo<WorkflowTabsContextValue>(
    () => ({
      tabs,
      runtimeByWorkflowId,
      recoveryNoticeByWorkflowId,
      openWorkflowTab,
      closeWorkflowTab,
      updateWorkflowTabName,
      setWorkflowRuntime,
      clearWorkflowRuntime,
      setWorkflowRecoveryNotice,
      dismissWorkflowRecoveryNotice,
    }),
    [
      clearWorkflowRuntime,
      dismissWorkflowRecoveryNotice,
      closeWorkflowTab,
      openWorkflowTab,
      recoveryNoticeByWorkflowId,
      setWorkflowRecoveryNotice,
      runtimeByWorkflowId,
      setWorkflowRuntime,
      tabs,
      updateWorkflowTabName,
    ],
  );

  return <WorkflowTabsContext.Provider value={value}>{children}</WorkflowTabsContext.Provider>;
}

function useWorkflowRuntimeProgressPolling(
  runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>,
  setWorkflowRuntime: (workflowId: string, update: Partial<WorkflowTabRuntimeState>) => void,
  setWorkflowRecoveryNotice: (workflowId: string, message: string) => void,
) {
  const runtimeRef = useRef(runtimeByWorkflowId);
  runtimeRef.current = runtimeByWorkflowId;
  const hasActiveRuntime = Object.values(runtimeByWorkflowId).some(shouldPollWorkflowRuntime);

  useEffect(() => {
    if (!hasActiveRuntime) return;
    let stopped = false;
    let inFlight = false;

    async function refreshActiveJobs() {
      if (inFlight) return;
      const activeEntries = Object.entries(runtimeRef.current).filter(([, runtime]) => shouldPollWorkflowRuntime(runtime));
      if (activeEntries.length === 0) return;

      inFlight = true;
      try {
        await Promise.all(
          activeEntries.map(async ([workflowId, runtime]) => {
            const jobId = runtime.activeJobId ?? runtime.queueId;
            if (!jobId) return;
            try {
              const progress = await fetchJobProgress(jobId);
              const currentRuntime = runtimeRef.current[workflowId];
              const currentJobId = currentRuntime?.activeJobId ?? currentRuntime?.queueId;
              if (!stopped && currentJobId === jobId) {
                if (progress.status === "unknown") {
                  setWorkflowRecoveryNotice(workflowId, vanishedRunRecoveryMessage());
                }
                setWorkflowRuntime(workflowId, workflowRuntimeUpdateFromProgress(progress));
              }
            } catch {
              // Keep the last known global progress visible through transient backend polling errors.
            }
          }),
        );
      } finally {
        inFlight = false;
      }
    }

    void refreshActiveJobs();
    const interval = window.setInterval(() => void refreshActiveJobs(), 1000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [hasActiveRuntime, setWorkflowRecoveryNotice, setWorkflowRuntime]);
}

function useWorkflowLeaseHeartbeat(
  runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>,
  setWorkflowRuntime: (workflowId: string, update: Partial<WorkflowTabRuntimeState>) => void,
) {
  const runtimeRef = useRef(runtimeByWorkflowId);
  runtimeRef.current = runtimeByWorkflowId;
  const hasHeldLease = Object.values(runtimeByWorkflowId).some((runtime) => Boolean(runtime.runnerLeaseId));

  useEffect(() => {
    if (!hasHeldLease) return;
    let stopped = false;
    let inFlight = false;
    let heartbeatPending = false;

    async function heartbeatHeldLeases() {
      if (inFlight) {
        heartbeatPending = true;
        return;
      }
      const heldLeases = Object.entries(runtimeRef.current)
        .flatMap(([workflowId, runtime]) => runtime.runnerLeaseId ? [[workflowId, runtime.runnerLeaseId] as const] : []);
      if (heldLeases.length === 0) return;
      inFlight = true;
      try {
        await Promise.all(heldLeases.map(async ([workflowId, leaseId]) => {
          try {
            const response = await heartbeatWorkflowRunnerLease(workflowId, leaseId);
            const current = runtimeRef.current[workflowId];
            if (stopped || current?.runnerLeaseId !== leaseId) return;
            if (response.status === "lease_not_found") {
              setWorkflowRuntime(workflowId, { runnerLeaseId: null, runnerId: null });
              return;
            }
            const runnerId = typeof response.runner?.runner_id === "string" ? response.runner.runner_id : null;
            if (runnerId && current.runnerId !== runnerId) {
              setWorkflowRuntime(workflowId, { runnerId });
            }
          } catch {
            // TTL is authoritative; transient heartbeat failures should not churn UI state.
          }
        }));
      } finally {
        inFlight = false;
        if (heartbeatPending && !stopped) {
          heartbeatPending = false;
          void heartbeatHeldLeases();
        }
      }
    }

    const heartbeatRestoredPage = (event: PageTransitionEvent) => {
      if (event.persisted) void heartbeatHeldLeases();
    };
    void heartbeatHeldLeases();
    const interval = window.setInterval(() => void heartbeatHeldLeases(), WORKFLOW_LEASE_HEARTBEAT_INTERVAL_MS);
    window.addEventListener("pageshow", heartbeatRestoredPage);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      window.removeEventListener("pageshow", heartbeatRestoredPage);
    };
  }, [hasHeldLease, setWorkflowRuntime]);
}

function useWorkflowLeasePagehideRelease(runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>) {
  const runtimeRef = useRef(runtimeByWorkflowId);
  runtimeRef.current = runtimeByWorkflowId;

  useEffect(() => {
    const releaseHeldLeases = () => {
      for (const [workflowId, runtime] of Object.entries(runtimeRef.current)) {
        if (runtime.runnerLeaseId) {
          closeWorkflowRunnerLeaseKeepalive(workflowId, runtime.runnerLeaseId);
        }
      }
    };
    window.addEventListener("pagehide", releaseHeldLeases);
    return () => window.removeEventListener("pagehide", releaseHeldLeases);
  }, []);
}

function shouldPollWorkflowRuntime(runtime: WorkflowTabRuntimeState) {
  if (runtime.handleSource === "runner_start_queue") return false;
  return hasActiveRuntimeHandle(runtime);
}

function hasActiveRuntimeHandle(runtime: WorkflowTabRuntimeState) {
  return Boolean(
    (runtime.activeJobId ?? runtime.queueId) &&
      runtime.activeJobStatus &&
      activeJobStatuses.has(runtime.activeJobStatus),
  );
}

function hasSessionOwnedRuntimeState(runtime: WorkflowTabRuntimeState) {
  return Boolean(
    runtime.activeJobId
      || runtime.queueId
      || runtime.runnerLeaseId
      || runtime.runnerId
      || runtime.handleSource
      || runtime.activeJobProgress?.job_id,
  );
}

function sessionOwnedWorkflowRuntimeIds(runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>) {
  return Object.entries(runtimeByWorkflowId)
    .filter(([, runtime]) => hasSessionOwnedRuntimeState(runtime))
    .map(([workflowId]) => workflowId);
}

function removeRuntimeStateForWorkflows(
  runtimeByWorkflowId: Record<string, WorkflowTabRuntimeState>,
  workflowIds: string[],
) {
  const workflowIdSet = new Set(workflowIds);
  let changed = false;
  const next = { ...runtimeByWorkflowId };
  for (const workflowId of workflowIdSet) {
    if (workflowId in next) {
      delete next[workflowId];
      changed = true;
    }
  }
  return changed ? next : runtimeByWorkflowId;
}

function recoveryNoticesForWorkflowIds(workflowIds: string[]) {
  return Object.fromEntries(
    workflowIds.map((workflowId) => [workflowId, APP_RESTARTED_RUN_MESSAGE]),
  );
}

function uniqueWorkflowIds(workflowIds: string[]) {
  return [...new Set(workflowIds.filter((workflowId) => Boolean(workflowId.trim())))];
}

function workflowRunHandleSnapshotFromRuntime(runtime: WorkflowTabRuntimeState) {
  const jobId = runtime.activeJobProgress?.job_id ?? runtime.activeJobId ?? runtime.queueId;
  const status = runtime.activeJobStatus ?? runtime.activeJobProgress?.status;
  if (!jobId || !status || status === "unknown") return null;
  return {
    jobId,
    queueId: runtime.activeJobProgress?.queue_id ?? runtime.queueId ?? null,
    status,
    updatedAt: runtime.activeJobUpdatedAt ?? Date.now(),
  };
}

function workflowRuntimeUpdateFromProgress(progress: JobProgress): Partial<WorkflowTabRuntimeState> {
  const now = Date.now();
  if (activeJobStatuses.has(progress.status)) {
    return {
      activeJobId: progress.job_id,
      activeJobStatus: progress.status,
      activeJobProgress: progress,
      activeJobUpdatedAt: now,
    };
  }
  return {
    activeJobId: null,
    activeJobStatus: progress.status,
    activeJobProgress: progress.status === "unknown" ? null : progress,
    activeJobUpdatedAt: now,
    handleSource: null,
    queueId: null,
  };
}

function runtimeUpdateWithRetainedNumericProgress(
  current: WorkflowTabRuntimeState,
  update: Partial<WorkflowTabRuntimeState>,
): Partial<WorkflowTabRuntimeState> {
  const incoming = update.activeJobProgress;
  const previous = current.activeJobProgress;
  if (
    !incoming
    || !previous
    || !activeJobStatuses.has(incoming.status)
    || hasNumericProgress(incoming)
    || !hasNumericProgress(previous)
    || !jobProgressHandlesMatch(previous, incoming)
  ) {
    return update;
  }
  return {
    ...update,
    activeJobProgress: {
      ...incoming,
      value: previous.value,
      max: previous.max,
    },
  };
}

function hasNumericProgress(progress: JobProgress) {
  return progress.value !== null
    && progress.value !== undefined
    && progress.max !== null
    && progress.max !== undefined
    && progress.max > 0;
}

function jobProgressHandlesMatch(left: JobProgress, right: JobProgress) {
  const leftHandles = new Set([left.job_id, left.queue_id].filter(Boolean));
  return [right.job_id, right.queue_id].some((handle) => Boolean(handle && leftHandles.has(handle)));
}

export function WorkflowTabsRouteProvider({
  activeWorkflowId,
  onActivateWorkflowTab,
  onRequestCloseWorkflowTab,
  children,
}: WorkflowTabsRouterContextValue & { children: ReactNode }) {
  const value = useMemo<WorkflowTabsRouterContextValue>(
    () => ({ activeWorkflowId, onActivateWorkflowTab, onRequestCloseWorkflowTab }),
    [activeWorkflowId, onActivateWorkflowTab, onRequestCloseWorkflowTab],
  );
  return <WorkflowTabsRouterContext.Provider value={value}>{children}</WorkflowTabsRouterContext.Provider>;
}

export function WorkflowTabsTopBar() {
  const tabsContext = useOptionalWorkflowTabs();
  const routerContext = useContext(WorkflowTabsRouterContext);
  if (!tabsContext || !routerContext || tabsContext.tabs.length === 0) {
    return <div className="topbar__tabs topbar__tabs--empty" aria-hidden="true" />;
  }

  return (
    <nav className="topbar__tabs" aria-label="Open workflows">
      <div className="workflow-tabs" role="list">
        {tabsContext.tabs.map((tab) => {
          const active = routerContext.activeWorkflowId === tab.workflowId;
          return (
            <div
              className={active ? "workflow-tab workflow-tab--active" : "workflow-tab"}
              role="listitem"
              key={tab.workflowId}
            >
              <button
                className="workflow-tab__label"
                type="button"
                aria-current={active ? "page" : undefined}
                title={tab.workflowName}
                onClick={() => routerContext.onActivateWorkflowTab(tab.workflowId, tab.workflowName)}
              >
                <span>{tab.workflowName}</span>
              </button>
              <button
                className="workflow-tab__close"
                type="button"
                aria-label={`Close ${tab.workflowName} workspace tab`}
                title="Close workspace tab"
                onClick={(event) => {
                  event.stopPropagation();
                  routerContext.onRequestCloseWorkflowTab(tab.workflowId);
                }}
              >
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </nav>
  );
}

export function useWorkflowTabs() {
  const context = useContext(WorkflowTabsContext);
  if (!context) {
    throw new Error("useWorkflowTabs must be used within WorkflowTabsProvider");
  }
  return context;
}

export function useOptionalWorkflowTabs() {
  return useContext(WorkflowTabsContext);
}

function loadStoredTabs(): WorkflowTab[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item): WorkflowTab[] => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (typeof record.workflowId !== "string" || !record.workflowId.trim()) return [];
      if (typeof record.workflowName !== "string" || !record.workflowName.trim()) return [];
      const lastActivatedAt = typeof record.lastActivatedAt === "number" ? record.lastActivatedAt : Date.now();
      return [{
        workflowId: record.workflowId,
        workflowName: record.workflowName,
        lastActivatedAt,
      }];
    });
  } catch {
    return [];
  }
}
