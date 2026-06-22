import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { fetchResourceSnapshot, type MachineResourceSnapshot } from "../../lib/api/noofyApi";

interface ResourceStatusState {
  snapshot: MachineResourceSnapshot | null;
  refreshError: string | null;
  lastCheckedAt: number | null;
  consecutiveFailures: number;
}

interface ResourceStatusContextValue extends ResourceStatusState {
  refreshResources: () => Promise<MachineResourceSnapshot | null>;
}

const RESOURCE_POLL_INTERVAL_MS = 5_000;

const ResourceStatusContext = createContext<ResourceStatusContextValue | null>(null);

const initialState: ResourceStatusState = {
  snapshot: null,
  refreshError: null,
  lastCheckedAt: null,
  consecutiveFailures: 0,
};

export function ResourceStatusProvider({
  children,
  initialSnapshot = null,
  skipInitialRefresh = false,
}: {
  children: ReactNode;
  initialSnapshot?: MachineResourceSnapshot | null;
  skipInitialRefresh?: boolean;
}) {
  const [state, setState] = useState<ResourceStatusState>({
    ...initialState,
    snapshot: initialSnapshot,
    lastCheckedAt: initialSnapshot ? Date.now() : null,
  });
  const mountedRef = useRef(false);
  const inFlightRef = useRef<Promise<MachineResourceSnapshot | null> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refreshResources = useCallback(async () => {
    if (inFlightRef.current) return inFlightRef.current;

    const request = fetchResourceSnapshot()
      .then((snapshot) => {
        if (mountedRef.current) {
          setState({
            snapshot,
            refreshError: null,
            lastCheckedAt: Date.now(),
            consecutiveFailures: 0,
          });
        }
        return snapshot;
      })
      .catch((error) => {
        if (mountedRef.current) {
          setState((current) => ({
            ...current,
            refreshError: resourceRefreshError(error),
            lastCheckedAt: Date.now(),
            consecutiveFailures: current.consecutiveFailures + 1,
          }));
        }
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

  useEffect(() => {
    if (skipInitialRefresh) return;
    void refreshResources();
    const interval = window.setInterval(() => {
      void refreshResources();
    }, RESOURCE_POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [refreshResources, skipInitialRefresh]);

  const value = useMemo<ResourceStatusContextValue>(
    () => ({
      ...state,
      refreshResources,
    }),
    [refreshResources, state],
  );

  return <ResourceStatusContext.Provider value={value}>{children}</ResourceStatusContext.Provider>;
}

export function useResourceStatus() {
  const context = useContext(ResourceStatusContext);
  if (!context) {
    throw new Error("useResourceStatus must be used within ResourceStatusProvider");
  }
  return context;
}

export function useOptionalResourceStatus() {
  return useContext(ResourceStatusContext);
}

function resourceRefreshError(error: unknown) {
  if (error instanceof Error) return error.message;
  return String(error);
}
