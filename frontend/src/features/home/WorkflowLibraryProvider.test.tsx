import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowLibraryProvider, useWorkflowLibrary } from "./WorkflowLibraryProvider";

const cachedWorkflow = {
  id: "cached",
  name: "Cached workflow",
  version: "1.0.0",
  description: "Previously loaded.",
};

const refreshedWorkflow = {
  id: "refreshed",
  name: "Refreshed workflow",
  version: "1.0.0",
  description: "Loaded by retry.",
};

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("WorkflowLibraryProvider", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("keeps cached workflows quiet after a transient failure and retries", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("temporary refresh failure"))
      .mockResolvedValueOnce(jsonResponse([refreshedWorkflow]));

    const { result } = renderHook(() => useWorkflowLibrary(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <WorkflowLibraryProvider
          initialWorkflowState={{
            workflows: [cachedWorkflow],
            hasLoaded: true,
            lastLoadedAt: Date.now(),
          }}
        >
          {children}
        </WorkflowLibraryProvider>
      ),
    });

    await act(async () => {
      await result.current.refreshWorkflows();
    });

    expect(result.current.workflows).toEqual([cachedWorkflow]);
    expect(result.current.error).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.workflows).toEqual([refreshedWorkflow]);
    expect(result.current.error).toBeNull();
  });

  it("reports an initial-load failure while retrying in the background", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("service unavailable"))
      .mockRejectedValueOnce(new Error("service still unavailable"))
      .mockResolvedValueOnce(jsonResponse([refreshedWorkflow]));

    const { result } = renderHook(() => useWorkflowLibrary(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <WorkflowLibraryProvider>{children}</WorkflowLibraryProvider>
      ),
    });

    await act(async () => {
      await result.current.refreshWorkflows();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.hasLoaded).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(result.current.error).toBe("service still unavailable");
    expect(result.current.hasLoaded).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });

    expect(result.current.hasLoaded).toBe(true);
    expect(result.current.workflows).toEqual([refreshedWorkflow]);
    expect(result.current.error).toBeNull();
  });

  it("times out a hung workflow request and releases the loading state", async () => {
    fetchMock.mockImplementation((_url, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const { result, unmount } = renderHook(() => useWorkflowLibrary(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <WorkflowLibraryProvider>{children}</WorkflowLibraryProvider>
      ),
    });

    let refresh!: Promise<unknown>;
    await act(async () => {
      refresh = result.current.refreshWorkflows();
      await vi.advanceTimersByTimeAsync(8_000);
      await refresh;
    });

    expect(result.current.refreshing).toBe(false);
    expect(result.current.error).toBeNull();

    unmount();
  });
});
