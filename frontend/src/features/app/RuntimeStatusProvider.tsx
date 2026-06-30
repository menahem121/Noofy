import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { fetchRuntimeStatus, type RuntimeStatus } from "../../lib/api/noofyApi";
import type { AppStatusView } from "./AppLayout";
import { recordBackendSessionRestart } from "./sessionRestore";

export type BackendStatus = "unknown" | "reachable" | "unreachable";
export type EngineStatus = "unknown" | "ready" | "installing" | "starting" | "busy" | "offline";

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
  pageRefreshRequired: boolean;
  backendSessionRecovery: BackendSessionRecoveryState | null;
  acknowledgeBackendSessionRecovery: (sequence?: number) => void;
  refreshPage: () => void;
  refreshRuntime: (options?: RefreshRuntimeOptions) => Promise<RuntimeStatus | null>;
  setRuntimeFromResponse: (runtime: RuntimeStatus | null) => void;
  markActionFailure: (error: unknown) => void;
}

export interface RefreshRuntimeOptions {
  force?: boolean;
  silent?: boolean;
  maxAgeMs?: number;
}

export interface BackendSessionRecoveryState {
  sequence: number;
  previousBackendSessionId: string;
  backendSessionId: string;
  detectedAt: number;
}

const DEFAULT_MAX_AGE_MS = 10_000;
const SILENT_FAILURE_THRESHOLD = 2;
const RUNTIME_REFRESH_TIMEOUT_MS = 8_000;
const ACTIVE_RUNTIME_POLL_INTERVAL_MS = 2_000;
const IDLE_RUNTIME_POLL_INTERVAL_MS = 10_000;

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
  reloadPage = defaultReloadPage,
}: {
  children: ReactNode;
  initialRuntimeState?: Partial<RuntimeHealthState>;
  skipInitialRefresh?: boolean;
  reloadPage?: () => void;
}) {
  const [state, setState] = useState<RuntimeHealthState>({
    ...initialState,
    ...initialRuntimeState,
  });
  const [pageRefreshRequired, setPageRefreshRequired] = useState(false);
  const [backendSessionRecovery, setBackendSessionRecovery] = useState<BackendSessionRecoveryState | null>(null);
  const requestSeqRef = useRef(0);
  const latestRequestSeqRef = useRef(0);
  const inFlightRef = useRef<Promise<RuntimeStatus | null> | null>(null);
  const stateRef = useRef(state);
  const backendSessionIdRef = useRef<string | null>(null);
  const backendSessionRecoverySeqRef = useRef(0);
  const backendSessionRecoveryRef = useRef<BackendSessionRecoveryState | null>(null);
  const refreshPage = useCallback(() => reloadPage(), [reloadPage]);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const observeBackendSession = useCallback((runtime: RuntimeStatus) => {
    const backendSessionId = runtime.backend_session_id?.trim();
    if (!backendSessionId) return;
    const previous = backendSessionIdRef.current;
    if (!previous) {
      backendSessionIdRef.current = backendSessionId;
      return;
    }
    if (previous === backendSessionId) {
      backendSessionIdRef.current = backendSessionId;
      return;
    }
    backendSessionIdRef.current = backendSessionId;
    const sequence = backendSessionRecoverySeqRef.current + 1;
    backendSessionRecoverySeqRef.current = sequence;
    const recovery = {
      sequence,
      previousBackendSessionId: previous,
      backendSessionId,
      detectedAt: Date.now(),
    };
    backendSessionRecoveryRef.current = recovery;
    setPageRefreshRequired(true);
    setBackendSessionRecovery(recovery);
    recordBackendSessionRestart(backendSessionId);
  }, []);

  const acknowledgeBackendSessionRecovery = useCallback((sequence?: number) => {
    const current = backendSessionRecoveryRef.current;
    if (sequence !== undefined && current?.sequence !== sequence) return;
    backendSessionRecoveryRef.current = null;
    setPageRefreshRequired(false);
    setBackendSessionRecovery(null);
  }, []);

  const setRuntimeFromResponse = useCallback((runtime: RuntimeStatus | null) => {
    if (runtime) observeBackendSession(runtime);
    setState((current) => {
      if (!runtime) return current;
      return stateFromRuntime(runtime, current);
    });
  }, [observeBackendSession]);

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
          observeBackendSession(runtime);
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
    [observeBackendSession],
  );

  useEffect(() => {
    if (skipInitialRefresh) return;
    void refreshRuntime({ force: true, silent: false });
  }, [refreshRuntime, skipInitialRefresh]);

  useEffect(() => {
    if (skipInitialRefresh) return;
    const pollIntervalMs = runtimePollIntervalMs(state);
    const timer = window.setTimeout(() => {
      void refreshRuntime({ maxAgeMs: 0, silent: true });
    }, pollIntervalMs);
    return () => window.clearTimeout(timer);
  }, [
    refreshRuntime,
    state.backendStatus,
    state.engineStatus,
    state.lastCheckedAt,
    skipInitialRefresh,
  ]);

  const value = useMemo<RuntimeStatusContextValue>(
    () => ({
      ...state,
      statusView: runtimeStatusView(state),
      pageRefreshRequired,
      backendSessionRecovery,
      acknowledgeBackendSessionRecovery,
      refreshPage,
      refreshRuntime,
      setRuntimeFromResponse,
      markActionFailure,
    }),
    [
      acknowledgeBackendSessionRecovery,
      backendSessionRecovery,
      markActionFailure,
      pageRefreshRequired,
      refreshPage,
      refreshRuntime,
      setRuntimeFromResponse,
      state,
    ],
  );

  return <RuntimeStatusContext.Provider value={value}>{children}</RuntimeStatusContext.Provider>;
}

function defaultReloadPage() {
  window.location.reload();
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
      label: "Checking Noofy",
      description: "Connecting to the local service",
      tone: "info",
      loading: true,
    };
  }

  if (state.backendStatus === "unreachable") {
    return {
      label: "Offline",
      description: state.refreshError ?? "Restart Noofy to reconnect",
      tone: "error",
    };
  }

  if (state.engineStatus === "ready") {
    return {
      label: "Ready",
      description: "Ready to run workflows",
      tone: "success",
    };
  }

  if (state.engineStatus === "starting") {
    return {
      label: "Starting",
      description: "Starting ComfyUI",
      tone: "info",
      loading: true,
    };
  }

  if (state.engineStatus === "installing") {
    return {
      label: "Installing",
      description: state.runtime?.environment_bootstrap_label ?? "Preparing ComfyUI",
      tone: "info",
      loading: true,
    };
  }

  if (state.engineStatus === "busy") {
    return {
      label: "Working",
      description: "Loading models or running a workflow",
      tone: "info",
      loading: true,
    };
  }

  if (state.engineStatus === "offline") {
    return {
      label: "ComfyUI offline",
      description: state.runtime?.error ?? "Open Engine Settings to start or repair ComfyUI",
      tone: "warning",
    };
  }

  return {
    label: "Connected",
    description: "Noofy is connected",
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
  if (runtime.environment_bootstrap_running) return "installing";
  if (runtime.reachable && runtime.transient_health_failure) return "busy";
  if (runtime.reachable) return "ready";
  if (runtime.sidecar_starting || runtime.managed_process_running) return "starting";
  return "offline";
}

function runtimePollIntervalMs(state: RuntimeHealthState) {
  if (
    state.backendStatus === "reachable" &&
    (state.engineStatus === "ready" || state.engineStatus === "busy")
  ) {
    return IDLE_RUNTIME_POLL_INTERVAL_MS;
  }
  return ACTIVE_RUNTIME_POLL_INTERVAL_MS;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function runtimeRefreshError(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") {
    return new Error("Noofy took too long to report its current status.");
  }
  return error;
}
