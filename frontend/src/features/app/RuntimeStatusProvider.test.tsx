import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  RuntimeStatusProvider,
  type RuntimeHealthState,
  runtimeStatusView,
  useRuntimeStatus,
} from "./RuntimeStatusProvider";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const readyRuntime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  sidecar_starting: false,
  pid: 123,
  error: null,
  environment: { prepared: true },
  crash_count: 0,
  restart_attempt: 0,
  max_restart_attempts: 3,
  uptime_seconds: 10,
  last_crash_at: null,
};

const busyRuntime = {
  ...readyRuntime,
  transient_health_failure: true,
  last_reachable_at: "2026-06-09T10:00:00+00:00",
  error: "health_check_timeout",
};

const startingRuntime = {
  ...readyRuntime,
  reachable: false,
  sidecar_starting: true,
};

const offlineRuntime = {
  ...readyRuntime,
  reachable: false,
  managed_process_running: false,
  sidecar_starting: false,
};

const readyRuntimeState: Partial<RuntimeHealthState> = {
  backendStatus: "reachable",
  engineStatus: "ready",
  runtime: readyRuntime as RuntimeHealthState["runtime"],
  hasKnownState: true,
  lastCheckedAt: Date.now() - 60_000,
};

function wrapper(initialRuntimeState?: Partial<RuntimeHealthState>) {
  return function TestWrapper({ children }: { children: ReactNode }) {
    return (
      <RuntimeStatusProvider initialRuntimeState={initialRuntimeState} skipInitialRefresh>
        {children}
      </RuntimeStatusProvider>
    );
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("RuntimeStatusProvider", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("maps an initial unknown state to Checking Noofy", () => {
    expect(runtimeStatusView({
      backendStatus: "unknown",
      engineStatus: "unknown",
      runtime: null,
      refreshing: true,
      refreshError: null,
      lastCheckedAt: null,
      consecutiveSilentFailures: 0,
      hasKnownState: false,
    }).label).toBe("Checking Noofy");
  });

  it("keeps Ready visible while a silent refresh is pending", async () => {
    const pending = deferred<Response>();
    fetchMock.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    void act(() => {
      void result.current.refreshRuntime({ silent: true });
    });

    await waitFor(() => expect(result.current.refreshing).toBe(true));
    expect(result.current.statusView.label).toBe("Ready");
  });

  it("keeps a last-known Ready status after one silent refresh failure", async () => {
    fetchMock.mockRejectedValue(new Error("temporary miss"));
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    await act(async () => {
      await result.current.refreshRuntime({ silent: true });
    });

    expect(result.current.statusView.label).toBe("Ready");
    expect(result.current.refreshError).toBe("temporary miss");
    expect(result.current.consecutiveSilentFailures).toBe(1);
  });

  it("marks the backend unreachable after two silent refresh failures", async () => {
    fetchMock.mockRejectedValue(new Error("still down"));
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    await act(async () => {
      await result.current.refreshRuntime({ silent: true });
      await result.current.refreshRuntime({ force: true, silent: true });
    });

    expect(result.current.backendStatus).toBe("unreachable");
    expect(result.current.statusView.label).toBe("Service offline");
  });

  it("marks the backend unreachable immediately after an action failure", () => {
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    act(() => {
      result.current.markActionFailure(new Error("run failed"));
    });

    expect(result.current.backendStatus).toBe("unreachable");
    expect(result.current.statusView.label).toBe("Service offline");
  });

  it("marks the backend unreachable when runtime status hangs", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_url, init?: RequestInit) => {
      const signal = init?.signal;
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(),
    });

    await act(async () => {
      const refresh = result.current.refreshRuntime({ force: true, silent: false });
      vi.advanceTimersByTime(8_000);
      await refresh;
    });
    vi.useRealTimers();

    expect(result.current.backendStatus).toBe("unreachable");
    expect(result.current.statusView.label).toBe("Service offline");
    expect(result.current.refreshError).toBe("Noofy's local app service did not answer runtime status in time.");
  });

  it("does not let an older failing refresh overwrite a newer successful one", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    fetchMock
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    let firstRefresh!: Promise<unknown>;
    let secondRefresh!: Promise<unknown>;
    await act(async () => {
      firstRefresh = result.current.refreshRuntime({ force: true, silent: true });
      secondRefresh = result.current.refreshRuntime({ force: true, silent: true });
      second.resolve(jsonResponse(readyRuntime));
      await secondRefresh;
    });
    await act(async () => {
      first.reject(new Error("old failure"));
      await firstRefresh;
    });

    expect(result.current.backendStatus).toBe("reachable");
    expect(result.current.statusView.label).toBe("Ready");
  });

  it("continues polling runtime status and updates when the engine becomes ready", async () => {
    vi.useFakeTimers();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(startingRuntime))
      .mockResolvedValueOnce(jsonResponse(readyRuntime));
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: ({ children }) => (
        <RuntimeStatusProvider
          initialRuntimeState={{
            backendStatus: "reachable",
            engineStatus: "starting",
            runtime: startingRuntime as RuntimeHealthState["runtime"],
            hasKnownState: true,
            lastCheckedAt: Date.now(),
          }}
        >
          {children}
        </RuntimeStatusProvider>
      ),
    });

    await act(async () => {});
    expect(result.current.statusView.label).toBe("Starting");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/runtime", expect.any(Object));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.statusView.label).toBe("Ready");
  });

  it("shows a transient ComfyUI health timeout as working instead of offline", async () => {
    fetchMock.mockResolvedValue(jsonResponse(busyRuntime));
    const { result } = renderHook(() => useRuntimeStatus(), {
      wrapper: wrapper(readyRuntimeState),
    });

    await act(async () => {
      await result.current.refreshRuntime({ force: true, silent: true });
    });

    expect(result.current.backendStatus).toBe("reachable");
    expect(result.current.engineStatus).toBe("busy");
    expect(result.current.statusView.label).toBe("Working");
  });

  it("distinguishes backend reachability from engine readiness", () => {
    expect(runtimeStatusView({
      ...readyRuntimeState,
      backendStatus: "reachable",
      engineStatus: "starting",
      runtime: startingRuntime as RuntimeHealthState["runtime"],
      refreshing: false,
      refreshError: null,
      lastCheckedAt: Date.now(),
      consecutiveSilentFailures: 0,
      hasKnownState: true,
    }).label).toBe("Starting");
    expect(runtimeStatusView({
      ...readyRuntimeState,
      backendStatus: "reachable",
      engineStatus: "busy",
      runtime: busyRuntime as RuntimeHealthState["runtime"],
      refreshing: false,
      refreshError: null,
      lastCheckedAt: Date.now(),
      consecutiveSilentFailures: 0,
      hasKnownState: true,
    }).label).toBe("Working");
    expect(runtimeStatusView({
      ...readyRuntimeState,
      backendStatus: "reachable",
      engineStatus: "offline",
      runtime: offlineRuntime as RuntimeHealthState["runtime"],
      refreshing: false,
      refreshError: null,
      lastCheckedAt: Date.now(),
      consecutiveSilentFailures: 0,
      hasKnownState: true,
    }).label).toBe("Engine offline");
  });
});
