import type { RequiredModelSummary } from "../../lib/api/noofyApi";
import { applyModelSummaryToWorkflowRunPageCache } from "./workflowRunPageCache";

export interface WorkflowModelSummaryUpdate {
  workflowId: string;
  summary: RequiredModelSummary;
}

type WorkflowModelSummaryListener = (update: WorkflowModelSummaryUpdate) => void;

const listeners = new Set<WorkflowModelSummaryListener>();

export function subscribeToWorkflowModelSummaryUpdates(listener: WorkflowModelSummaryListener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// The globally-owned missing-models modal refreshes summaries for workflows it
// does not render. Publishing must also update the cached run-page state so a
// workflow reopened later is consistent even when no run page is mounted.
export function publishWorkflowModelSummaryUpdate(update: WorkflowModelSummaryUpdate) {
  applyModelSummaryToWorkflowRunPageCache(update.workflowId, update.summary);
  for (const listener of [...listeners]) {
    listener(update);
  }
}

export function resetWorkflowModelSummaryListenersForTests() {
  listeners.clear();
}
