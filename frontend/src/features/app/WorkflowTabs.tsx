import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";

import { fetchJobProgress, type JobProgress, type JobStatus } from "../../lib/api/noofyApi";

const STORAGE_KEY = "noofy.workflowTabs.v1";
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
  openWorkflowTab: (workflowId: string, workflowName?: string) => void;
  closeWorkflowTab: (workflowId: string) => void;
  updateWorkflowTabName: (workflowId: string, workflowName: string) => void;
  setWorkflowRuntime: (workflowId: string, update: Partial<WorkflowTabRuntimeState>) => void;
  clearWorkflowRuntime: (workflowId: string) => void;
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
  const [tabs, setTabs] = useState<WorkflowTab[]>(() => loadStoredTabs());
  const [runtimeByWorkflowId, setRuntimeByWorkflowId] = useState<Record<string, WorkflowTabRuntimeState>>({});

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs));
    } catch {
      // Persistent tabs are a convenience; runtime should not depend on them.
    }
  }, [tabs]);

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
    setRuntimeByWorkflowId((current) => {
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
    setRuntimeByWorkflowId((current) => ({
      ...current,
      [workflowId]: {
        ...(current[workflowId] ?? emptyRuntimeState),
        ...update,
      },
    }));
  }, []);

  const clearWorkflowRuntime = useCallback((workflowId: string) => {
    setRuntimeByWorkflowId((current) => {
      if (!(workflowId in current)) return current;
      const next = { ...current };
      delete next[workflowId];
      return next;
    });
  }, []);

  useWorkflowRuntimeProgressPolling(runtimeByWorkflowId, setWorkflowRuntime);

  const value = useMemo<WorkflowTabsContextValue>(
    () => ({
      tabs,
      runtimeByWorkflowId,
      openWorkflowTab,
      closeWorkflowTab,
      updateWorkflowTabName,
      setWorkflowRuntime,
      clearWorkflowRuntime,
    }),
    [
      clearWorkflowRuntime,
      closeWorkflowTab,
      openWorkflowTab,
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
  }, [hasActiveRuntime, setWorkflowRuntime]);
}

function shouldPollWorkflowRuntime(runtime: WorkflowTabRuntimeState) {
  if (runtime.handleSource === "runner_start_queue") return false;
  return Boolean(
    (runtime.activeJobId ?? runtime.queueId) &&
      runtime.activeJobStatus &&
      activeJobStatuses.has(runtime.activeJobStatus),
  );
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
    activeJobProgress: progress,
    activeJobUpdatedAt: now,
    handleSource: null,
    queueId: null,
  };
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
