import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";

import {
  useWorkflowTabs,
  WorkflowTabsProvider,
  WorkflowTabsRouteProvider,
  WorkflowTabsTopBar,
} from "./WorkflowTabs";
import { RuntimeStatusProvider, useRuntimeStatus } from "./RuntimeStatusProvider";

function Harness({ activeWorkflowId = "wf-1" }: { activeWorkflowId?: string | null }) {
  const tabs = useWorkflowTabs();
  return (
    <WorkflowTabsRouteProvider
      activeWorkflowId={activeWorkflowId}
      onActivateWorkflowTab={(workflowId, workflowName) => tabs.openWorkflowTab(workflowId, workflowName)}
      onRequestCloseWorkflowTab={(workflowId) => tabs.closeWorkflowTab(workflowId)}
    >
      <button type="button" onClick={() => tabs.openWorkflowTab("wf-1", "Very Long Workflow Name That Needs Truncation")}>
        Open one
      </button>
      <button type="button" onClick={() => tabs.openWorkflowTab("wf-1", "Very Long Workflow Name That Needs Truncation")}>
        Open duplicate
      </button>
      <button type="button" onClick={() => tabs.openWorkflowTab("wf-2", "Second Workflow")}>
        Open two
      </button>
      <button
        type="button"
        onClick={() =>
          tabs.setWorkflowRuntime("wf-1", {
            activeJobId: "job-1",
            activeJobStatus: "running",
            handleSource: "job",
            runnerLeaseId: "lease-1",
            runnerId: "runner-1",
          })
        }
      >
        Set runtime
      </button>
      <button
        type="button"
        onClick={() =>
          tabs.setWorkflowRuntime("wf-1", {
            runnerLeaseId: "lease-1",
            runnerId: "runner-1",
          })
        }
      >
        Set lease
      </button>
      <button
        type="button"
        onClick={() => tabs.setWorkflowRuntime("wf-1", {
          activeJobId: "job-1",
          activeJobStatus: "running",
          activeJobProgress: {
            job_id: "job-1",
            status: "running",
            value: 6,
            max: 10,
            current_node: "3",
            message: "Generating...",
          },
        })}
      >
        Set numeric progress
      </button>
      <button
        type="button"
        onClick={() => tabs.setWorkflowRuntime("wf-1", {
          activeJobId: "job-1",
          activeJobStatus: "running",
          activeJobProgress: {
            job_id: "job-1",
            status: "running",
            value: null,
            max: null,
            current_node: null,
            message: "Loading the next model...",
          },
        })}
      >
        Set progress without estimate
      </button>
      <span data-testid="lease-id">{tabs.runtimeByWorkflowId["wf-1"]?.runnerLeaseId ?? "none"}</span>
      <span data-testid="progress-value">{tabs.runtimeByWorkflowId["wf-1"]?.activeJobProgress?.value ?? "none"}</span>
      <span data-testid="progress-max">{tabs.runtimeByWorkflowId["wf-1"]?.activeJobProgress?.max ?? "none"}</span>
      <span data-testid="progress-message">{tabs.runtimeByWorkflowId["wf-1"]?.activeJobProgress?.message ?? "none"}</span>
      <span data-testid="recovery-notice">{tabs.recoveryNoticeByWorkflowId["wf-1"] ?? "none"}</span>
      <WorkflowTabsTopBar />
    </WorkflowTabsRouteProvider>
  );
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
} as const;

function BackendSessionHarness() {
  const runtimeStatus = useRuntimeStatus();
  const tabs = useWorkflowTabs();
  return (
    <>
      <button
        type="button"
        onClick={() => runtimeStatus.setRuntimeFromResponse({ ...readyRuntime, backend_session_id: "bs-old" })}
      >
        Adopt old session
      </button>
      <button
        type="button"
        onClick={() =>
          tabs.setWorkflowRuntime("wf-1", {
            activeJobId: "job-old",
            activeJobStatus: "queued",
            handleSource: "runner_start_queue",
          })
        }
      >
        Set stale runtime
      </button>
      <button
        type="button"
        onClick={() => runtimeStatus.setRuntimeFromResponse({ ...readyRuntime, backend_session_id: "bs-new" })}
      >
        Restart backend
      </button>
      <span data-testid="page-refresh-required">{String(runtimeStatus.pageRefreshRequired)}</span>
      <span data-testid="active-job">{tabs.runtimeByWorkflowId["wf-1"]?.activeJobId ?? "none"}</span>
      <span data-testid="recovery-notice">{tabs.recoveryNoticeByWorkflowId["wf-1"] ?? "none"}</span>
    </>
  );
}

describe("WorkflowTabs", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("opens tabs, avoids duplicates, and marks the active route tab", () => {
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open one" }));
    fireEvent.click(screen.getByRole("button", { name: "Open duplicate" }));
    fireEvent.click(screen.getByRole("button", { name: "Open two" }));

    expect(screen.getAllByRole("button", { name: "Close Very Long Workflow Name That Needs Truncation workspace tab" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Very Long Workflow Name That Needs Truncation" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Second Workflow" })).not.toHaveAttribute("aria-current");
  });

  it("persists only stable shortcut data", () => {
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open one" }));
    fireEvent.click(screen.getByRole("button", { name: "Set runtime" }));

    const stored = window.localStorage.getItem("noofy.workflowTabs.v1");
    expect(stored).toContain("wf-1");
    expect(stored).toContain("Very Long Workflow Name That Needs Truncation");
    expect(stored).not.toContain("job-1");
    expect(stored).not.toContain("lease-1");
    expect(stored).not.toContain("runner-1");
    const activeRuns = window.sessionStorage.getItem("noofy.activeRunWorkflows.v1");
    expect(activeRuns).toContain("wf-1");
    expect(activeRuns).not.toContain("job-1");
    expect(activeRuns).not.toContain("lease-1");
    const storedRunHandles = JSON.parse(window.sessionStorage.getItem("noofy.workflowRunHandles.v1") ?? "{}");
    expect(storedRunHandles.handles["wf-1"]).toMatchObject({
      workflowId: "wf-1",
      jobId: "job-1",
      queueId: null,
      status: "running",
    });
    expect(JSON.stringify(storedRunHandles)).not.toContain("lease-1");
  });

  it("clears the stored workflow run handle when a tab closes", async () => {
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open one" }));
    fireEvent.click(screen.getByRole("button", { name: "Set runtime" }));

    await waitFor(() => {
      expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toContain("job-1");
    });

    fireEvent.click(screen.getByRole("button", { name: "Close Very Long Workflow Name That Needs Truncation workspace tab" }));

    await waitFor(() => {
      expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toBeNull();
    });
  });

  it("retains the last numeric percentage when an active update has no estimate", () => {
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Set numeric progress" }));
    fireEvent.click(screen.getByRole("button", { name: "Set progress without estimate" }));

    expect(screen.getByTestId("progress-value")).toHaveTextContent("6");
    expect(screen.getByTestId("progress-max")).toHaveTextContent("10");
    expect(screen.getByTestId("progress-message")).toHaveTextContent("Loading the next model...");
  });

  it("restores only a calm recovery notice for active workflows after a backend restart", () => {
    window.sessionStorage.setItem(
      "noofy.sessionRestart.v1",
      JSON.stringify({ backendSessionId: "bs-new", detectedAt: Date.now() }),
    );
    window.sessionStorage.setItem(
      "noofy.activeRunWorkflows.v1",
      JSON.stringify({ workflowIds: ["wf-1"], updatedAt: Date.now() }),
    );
    window.sessionStorage.setItem(
      "noofy.workflowRunHandles.v1",
      JSON.stringify({
        handles: {
          "wf-1": {
            workflowId: "wf-1",
            jobId: "job-from-old-backend",
            queueId: null,
            status: "running",
            updatedAt: Date.now(),
          },
        },
      }),
    );

    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    expect(screen.getByTestId("recovery-notice")).toHaveTextContent(
      "The app restarted. Run this workflow again when ready.",
    );
    expect(window.sessionStorage.getItem("noofy.activeRunWorkflows.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.sessionRestart.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toBeNull();
  });

  it("restores a recovery notice from a pending run before any job handle exists", () => {
    window.sessionStorage.setItem(
      "noofy.sessionRestart.v1",
      JSON.stringify({ backendSessionId: "bs-new", detectedAt: Date.now() }),
    );
    window.sessionStorage.setItem(
      "noofy.pendingRunWorkflows.v1",
      JSON.stringify({ workflowIds: ["wf-1"], updatedAt: Date.now() }),
    );

    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );

    expect(screen.getByTestId("recovery-notice")).toHaveTextContent(
      "The app restarted. Run this workflow again when ready.",
    );
    expect(window.sessionStorage.getItem("noofy.pendingRunWorkflows.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.activeRunWorkflows.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.sessionRestart.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toBeNull();
  });

  it("clears live run handles and acknowledges recovery when the backend session changes", async () => {
    render(
      <RuntimeStatusProvider skipInitialRefresh>
        <WorkflowTabsProvider>
          <BackendSessionHarness />
        </WorkflowTabsProvider>
      </RuntimeStatusProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Adopt old session" }));
    fireEvent.click(screen.getByRole("button", { name: "Set stale runtime" }));

    await waitFor(() => {
      expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toContain("job-old");
    });

    fireEvent.click(screen.getByRole("button", { name: "Restart backend" }));

    await waitFor(() => {
      expect(screen.getByTestId("recovery-notice")).toHaveTextContent(
        "The app restarted. Run this workflow again when ready.",
      );
    });
    expect(screen.getByTestId("active-job")).toHaveTextContent("none");
    expect(screen.getByTestId("page-refresh-required")).toHaveTextContent("false");
    expect(window.sessionStorage.getItem("noofy.activeRunWorkflows.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.sessionRestart.v1")).toBeNull();
    expect(window.sessionStorage.getItem("noofy.workflowRunHandles.v1")).toBeNull();
  });

  it("heartbeats a newly held lease immediately and clears a lease the backend no longer knows", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      workflow_id: "wf-1",
      status: "lease_not_found",
      lease_id: "lease-1",
      runner: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Set lease" }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/wf-1/runner/leases/lease-1/heartbeat",
      expect.objectContaining({ method: "PUT" }),
    );
    expect(screen.getByTestId("lease-id")).toHaveTextContent("none");
  });

  it("rechecks a held lease immediately when a bfcache page is restored", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        workflow_id: "wf-1",
        status: "active",
        lease_id: "lease-1",
        runner: { runner_id: "runner-1" },
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        workflow_id: "wf-1",
        status: "lease_not_found",
        lease_id: "lease-1",
        runner: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Set lease" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    fireEvent(window, new PageTransitionEvent("pageshow", { persisted: true }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(screen.getByTestId("lease-id")).toHaveTextContent("none");
    });
  });

  it("attempts keepalive lease close on pagehide", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <WorkflowTabsProvider>
        <Harness />
      </WorkflowTabsProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Set lease" }));
    fireEvent(window, new Event("pagehide"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/wf-1/runner/leases/lease-1",
        expect.objectContaining({ method: "DELETE", keepalive: true }),
      );
    });
  });
});
