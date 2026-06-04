import { describe, expect, it } from "vitest";

import type { WorkflowSummary } from "../../lib/api/noofyApi";
import { workflowStatus, workflowStatusLabel } from "./workflowSearch";

function workflow(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    id: "workflow-1",
    name: "Workflow",
    version: "1.0.0",
    description: "",
    ...overrides,
  };
}

describe("workflowSearch status", () => {
  it("keeps unfinished dashboard setup marked Configure", () => {
    const unfinished = workflow({
      status: "prepared_needs_input_setup",
      status_label: "Prepared",
      dashboard_status: "not_configured",
      dashboard_ready: false,
      unresolved_input_count: 1,
    });

    expect(workflowStatus(unfinished)).toBe("need_setup");
    expect(workflowStatusLabel(unfinished)).toBe("Configure");
  });

  it("shows configured workflows as ready", () => {
    const configured = workflow({
      status: "installed",
      dashboard_status: "configured",
      dashboard_ready: true,
      unresolved_input_count: 0,
    });

    expect(workflowStatus(configured)).toBe("ready");
    expect(workflowStatusLabel(configured)).toBe("Ready");
  });
});
