import { clearDashboardDraft } from "../dashboard-builder/dashboardBuilderContent";
import { removePendingImportedSetupReminder } from "../home/pendingSetupBanners";
import { invalidateWorkflowUserStateCache } from "../../lib/useWorkflowUserState";
import { invalidateWorkflowRunPageCache } from "./WorkflowRunPage";

export function cleanupRemovedWorkflowFrontendState(
  workflowId: string,
  workflowTabs?: { closeWorkflowTab: (workflowId: string) => void } | null,
) {
  for (const cleanup of [
    () => clearDashboardDraft(workflowId),
    () => removePendingImportedSetupReminder(workflowId),
    () => invalidateWorkflowUserStateCache(workflowId),
    () => invalidateWorkflowRunPageCache(workflowId),
    () => workflowTabs?.closeWorkflowTab(workflowId),
  ]) {
    try {
      cleanup();
    } catch {
      // Backend deletion is authoritative; one local cache failure must not
      // prevent the remaining deleted-workflow state from being invalidated.
    }
  }
}
