import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";

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

  const setWorkflowsFromResponse = useCallback((workflows: WorkflowSummary[]) => {
    setState((current) => ({
      ...current,
      workflows,
      refreshing: false,
      error: null,
      lastLoadedAt: Date.now(),
      hasLoaded: true,
    }));
  }, []);

  const updateWorkflowFromResponse = useCallback((workflow: WorkflowSummary) => {
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
  }, []);

  const refreshWorkflows = useCallback(async () => {
    if (inFlightRef.current) return inFlightRef.current;

    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    latestRequestSeqRef.current = requestSeq;
    setState((current) => ({ ...current, refreshing: true }));

    const request = fetchWorkflows()
      .then((workflows) => {
        if (requestSeq !== latestRequestSeqRef.current) return workflows;
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
        setState((current) => ({
          ...current,
          refreshing: false,
          error: error instanceof Error ? error.message : String(error),
        }));
        return null;
      })
      .finally(() => {
        if (inFlightRef.current === request) {
          inFlightRef.current = null;
        }
      });

    inFlightRef.current = request;
    return request;
  }, []);

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
