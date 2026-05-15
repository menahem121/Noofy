import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import type { AppStatusView } from "./AppLayout";

export type BackendStatus = "unknown" | "reachable" | "unreachable";
export type EngineStatus = "unknown" | "ready" | "starting" | "offline";

export interface RuntimeHealthState {
  backendStatus: BackendStatus;
  engineStatus: EngineStatus;
  runtime: RuntimeStatus | null;
  refreshing: boolean;
  refreshError: string | null;
  lastCheckedAt: number | null;
  consecutiveSilentFailures: number;
  hasKnownState: boolean;
}

interface RuntimeStatusContextValue extends RuntimeHealthState {
  statusView: AppStatusView;
  refreshRuntime: (options?: RefreshRuntimeOptions) => Promise<RuntimeStatus | null>;
  setRuntimeFromResponse: (runtime: RuntimeStatus | null) => void;
  markActionFailure: (error: unknown) => void;
}

export interface RefreshRuntimeOptions {
  force?: boolean;
  silent?: boolean;
  maxAgeMs?: number;
}

const DEFAULT_MAX_AGE_MS = 10_000;
const SILENT_FAILURE_THRESHOLD = 2;
const RUNTIME_REFRESH_TIMEOUT_MS = 8_000;

const RuntimeStatusContext = createContext<RuntimeStatusContextValue | null>(null);

const initialState: RuntimeHealthState = {
  backendStatus: "unknown",
  engineStatus: "unknown",
  runtime: null,
  refreshing: false,
  refreshError: null,
  lastCheckedAt: null,
  consecutiveSilentFailures: 0,
  hasKnownState: false,
};

export function RuntimeStatusProvider({
  children,
  initialRuntimeState,
  skipInitialRefresh = false,
}: {
  children: ReactNode;
  initialRuntimeState?: Partial<RuntimeHealthState>;
  skipInitialRefresh?: boolean;
}) {
  const [state, setState] = useState<RuntimeHealthState>({
    ...initialState,
    ...initialRuntimeState,
  });
  const requestSeqRef = useRef(0);
  const latestRequestSeqRef = useRef(0);
  const inFlightRef = useRef<Promise<RuntimeStatus | null> | null>(null);
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const setRuntimeFromResponse = useCallback((runtime: RuntimeStatus | null) => {
    setState((current) => {
      if (!runtime) return current;
      return stateFromRuntime(runtime, current);
    });
  }, []);

  const markActionFailure = useCallback((error: unknown) => {
    setState((current) => ({
      ...current,
      backendStatus: "unreachable",
      engineStatus: "offline",
      refreshing: false,
      refreshError: errorMessage(error),
      consecutiveSilentFailures: SILENT_FAILURE_THRESHOLD,
      hasKnownState: true,
      lastCheckedAt: Date.now(),
    }));
  }, []);

  const refreshRuntime = useCallback(
    async (options: RefreshRuntimeOptions = {}) => {
      const { force = false, silent = true, maxAgeMs = DEFAULT_MAX_AGE_MS } = options;
      const current = stateRef.current;
      const freshEnough = current.lastCheckedAt !== null && Date.now() - current.lastCheckedAt < maxAgeMs;
      if (!force && freshEnough) return current.runtime;
      if (!force && inFlightRef.current) return inFlightRef.current;

      const requestSeq = requestSeqRef.current + 1;
      requestSeqRef.current = requestSeq;
      latestRequestSeqRef.current = requestSeq;

      setState((stateBeforeRefresh) => ({
        ...stateBeforeRefresh,
        refreshing: true,
        refreshError: silent ? stateBeforeRefresh.refreshError : null,
      }));

      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), RUNTIME_REFRESH_TIMEOUT_MS);
      const request = fetchRuntimeStatus({ signal: controller.signal })
        .then((runtime) => {
          if (requestSeq !== latestRequestSeqRef.current) return runtime;
          setState((stateBeforeSuccess) => stateFromRuntime(runtime, stateBeforeSuccess));
          return runtime;
        })
        .catch((error) => {
          if (requestSeq !== latestRequestSeqRef.current) return null;
          setState((stateBeforeFailure) => stateFromFailure(stateBeforeFailure, runtimeRefreshError(error), silent));
          return null;
        })
        .finally(() => {
          window.clearTimeout(timeout);
          if (inFlightRef.current === request) {
            inFlightRef.current = null;
          }
        });

      inFlightRef.current = request;
      return request;
    },
    [],
  );

  useEffect(() => {
    if (skipInitialRefresh) return;
    void refreshRuntime({ force: true, silent: false });
  }, [refreshRuntime, skipInitialRefresh]);

  const value = useMemo<RuntimeStatusContextValue>(
    () => ({
      ...state,
      statusView: runtimeStatusView(state),
      refreshRuntime,
      setRuntimeFromResponse,
      markActionFailure,
    }),
    [markActionFailure, refreshRuntime, setRuntimeFromResponse, state],
  );

  return <RuntimeStatusContext.Provider value={value}>{children}</RuntimeStatusContext.Provider>;
}

export function useRuntimeStatus() {
  const context = useContext(RuntimeStatusContext);
  if (!context) {
    throw new Error("useRuntimeStatus must be used within RuntimeStatusProvider");
  }
  return context;
}

export function useOptionalRuntimeStatus() {
  return useContext(RuntimeStatusContext);
}

export function runtimeStatusView(state: RuntimeHealthState): AppStatusView {
  if (state.backendStatus === "unknown") {
    return {
      label: "Checking backend",
      description: "Looking for the local app service",
      tone: "info",
      loading: true,
    };
  }

  if (state.backendStatus === "unreachable") {
    return {
      label: "Backend offline",
      description: state.refreshError ?? "Start the Noofy backend to load live workflows",
      tone: "error",
    };
  }

  if (state.engineStatus === "ready") {
    return {
      label: "Ready",
      description: "Local workflow engine is reachable",
      tone: "success",
    };
  }

  if (state.engineStatus === "starting") {
    return {
      label: "Starting",
      description: "The local engine process is still warming up",
      tone: "info",
      loading: true,
    };
  }

  if (state.engineStatus === "offline") {
    return {
      label: "Engine offline",
      description: state.runtime?.error ?? "Open settings to start or repair the local engine",
      tone: "warning",
    };
  }

  return {
    label: "Backend reachable",
    description: "Noofy is connected to the local backend",
    tone: "info",
  };
}

function stateFromRuntime(runtime: RuntimeStatus, current: RuntimeHealthState): RuntimeHealthState {
  return {
    ...current,
    backendStatus: "reachable",
    engineStatus: engineStatusFromRuntime(runtime),
    runtime,
    refreshing: false,
    refreshError: null,
    lastCheckedAt: Date.now(),
    consecutiveSilentFailures: 0,
    hasKnownState: true,
  };
}

function stateFromFailure(current: RuntimeHealthState, error: unknown, silent: boolean): RuntimeHealthState {
  const nextFailures = silent ? current.consecutiveSilentFailures + 1 : SILENT_FAILURE_THRESHOLD;
  const preserveLastKnown = silent && current.runtime && nextFailures < SILENT_FAILURE_THRESHOLD;
  return {
    ...current,
    backendStatus: preserveLastKnown ? current.backendStatus : "unreachable",
    engineStatus: preserveLastKnown ? current.engineStatus : "offline",
    refreshing: false,
    refreshError: errorMessage(error),
    lastCheckedAt: Date.now(),
    consecutiveSilentFailures: nextFailures,
    hasKnownState: current.hasKnownState || !preserveLastKnown,
  };
}

function engineStatusFromRuntime(runtime: RuntimeStatus): EngineStatus {
  if (runtime.reachable) return "ready";
  if (runtime.sidecar_starting || runtime.managed_process_running) return "starting";
  return "offline";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function runtimeRefreshError(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return new Error("Noofy backend did not answer runtime status in time.");
  }
  return error;
}
