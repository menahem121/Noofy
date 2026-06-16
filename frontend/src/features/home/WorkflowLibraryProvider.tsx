import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { fetchWorkflows, type WorkflowSummary } from "../../lib/api/noofyApi";

interface WorkflowLibraryState {
  workflows: WorkflowSummary[];
  refreshing: boolean;
  error: string | null;
  lastLoadedAt: number | null;
  hasLoaded: boolean;
}

interface WorkflowLibraryContextValue extends WorkflowLibraryState {
  refreshWorkflows: () => Promise<WorkflowSummary[] | null>;
  setWorkflowsFromResponse: (workflows: WorkflowSummary[]) => void;
  updateWorkflowFromResponse: (workflow: WorkflowSummary) => void;
}

const WorkflowLibraryContext = createContext<WorkflowLibraryContextValue | null>(null);

const initialState: WorkflowLibraryState = {
  workflows: [],
  refreshing: false,
  error: null,
  lastLoadedAt: null,
  hasLoaded: false,
};

const REFRESH_RETRY_DELAYS_MS = [1_000, 3_000, 10_000, 30_000];
const VISIBLE_FAILURE_THRESHOLD = 2;
const WORKFLOW_REFRESH_TIMEOUT_MS = 8_000;

export function WorkflowLibraryProvider({
  children,
  initialWorkflowState,
}: {
  children: ReactNode;
  initialWorkflowState?: Partial<WorkflowLibraryState>;
}) {
  const [state, setState] = useState<WorkflowLibraryState>({
    ...initialState,
    ...initialWorkflowState,
  });
  const requestSeqRef = useRef(0);
  const latestRequestSeqRef = useRef(0);
  const inFlightRef = useRef<Promise<WorkflowSummary[] | null> | null>(null);
  const retryAttemptRef = useRef(0);
  const consecutiveFailureRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const refreshWorkflowsRef = useRef<() => Promise<WorkflowSummary[] | null>>(async () => null);
  const mountedRef = useRef(true);

  const clearScheduledRetry = useCallback(() => {
    if (retryTimerRef.current === null) return;
    window.clearTimeout(retryTimerRef.current);
    retryTimerRef.current = null;
  }, []);

  const scheduleRetry = useCallback(() => {
    if (!mountedRef.current || retryTimerRef.current !== null) return;
    const delayIndex = Math.min(retryAttemptRef.current, REFRESH_RETRY_DELAYS_MS.length - 1);
    retryAttemptRef.current += 1;
    retryTimerRef.current = window.setTimeout(() => {
      retryTimerRef.current = null;
      void refreshWorkflowsRef.current();
    }, REFRESH_RETRY_DELAYS_MS[delayIndex]);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearScheduledRetry();
      requestControllerRef.current?.abort();
    };
  }, [clearScheduledRetry]);

  const setWorkflowsFromResponse = useCallback((workflows: WorkflowSummary[]) => {
    clearScheduledRetry();
    retryAttemptRef.current = 0;
    consecutiveFailureRef.current = 0;
    setState((current) => ({
      ...current,
      workflows,
      refreshing: false,
      error: null,
      lastLoadedAt: Date.now(),
      hasLoaded: true,
    }));
  }, [clearScheduledRetry]);

  const updateWorkflowFromResponse = useCallback((workflow: WorkflowSummary) => {
    clearScheduledRetry();
    retryAttemptRef.current = 0;
    consecutiveFailureRef.current = 0;
    setState((current) => {
      const existingIndex = current.workflows.findIndex((item) => item.id === workflow.id);
      const workflows =
        existingIndex >= 0
          ? current.workflows.map((item) => (item.id === workflow.id ? workflow : item))
          : [...current.workflows, workflow];
      return {
        ...current,
        workflows,
        error: null,
        lastLoadedAt: Date.now(),
        hasLoaded: true,
      };
    });
  }, [clearScheduledRetry]);

  const refreshWorkflows = useCallback(async () => {
    if (inFlightRef.current) return inFlightRef.current;

    clearScheduledRetry();
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    latestRequestSeqRef.current = requestSeq;
    setState((current) => ({ ...current, refreshing: true }));

    const controller = new AbortController();
    requestControllerRef.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), WORKFLOW_REFRESH_TIMEOUT_MS);
    const request = fetchWorkflows({ signal: controller.signal })
      .then((workflows) => {
        if (requestSeq !== latestRequestSeqRef.current) return workflows;
        retryAttemptRef.current = 0;
        consecutiveFailureRef.current = 0;
        setState((current) => ({
          ...current,
          workflows,
          refreshing: false,
          error: null,
          lastLoadedAt: Date.now(),
          hasLoaded: true,
        }));
        return workflows;
      })
      .catch((error) => {
        if (requestSeq !== latestRequestSeqRef.current) return null;
        consecutiveFailureRef.current += 1;
        const refreshError = workflowRefreshError(error);
        setState((current) => ({
          ...current,
          refreshing: false,
          error:
            current.hasLoaded || consecutiveFailureRef.current < VISIBLE_FAILURE_THRESHOLD
              ? null
              : refreshError.message,
        }));
        scheduleRetry();
        return null;
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (requestControllerRef.current === controller) {
          requestControllerRef.current = null;
        }
        if (inFlightRef.current === request) {
          inFlightRef.current = null;
        }
      });

    inFlightRef.current = request;
    return request;
  }, [clearScheduledRetry, scheduleRetry]);

  refreshWorkflowsRef.current = refreshWorkflows;

  const value = useMemo<WorkflowLibraryContextValue>(
    () => ({
      ...state,
      refreshWorkflows,
      setWorkflowsFromResponse,
      updateWorkflowFromResponse,
    }),
    [refreshWorkflows, setWorkflowsFromResponse, state, updateWorkflowFromResponse],
  );

  return <WorkflowLibraryContext.Provider value={value}>{children}</WorkflowLibraryContext.Provider>;
}

export function useWorkflowLibrary() {
  const context = useContext(WorkflowLibraryContext);
  if (!context) {
    throw new Error("useWorkflowLibrary must be used within WorkflowLibraryProvider");
  }
  return context;
}

function workflowRefreshError(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return new Error("Noofy took too long to load your workflows.");
  }
  return error instanceof Error ? error : new Error(String(error));
}
