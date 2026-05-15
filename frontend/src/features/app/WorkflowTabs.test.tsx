import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, beforeEach, afterEach } from "vitest";

import {
  useWorkflowTabs,
  WorkflowTabsProvider,
  WorkflowTabsRouteProvider,
  WorkflowTabsTopBar,
} from "./WorkflowTabs";

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
      <WorkflowTabsTopBar />
    </WorkflowTabsRouteProvider>
  );
}

describe("WorkflowTabs", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
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
  });
});
