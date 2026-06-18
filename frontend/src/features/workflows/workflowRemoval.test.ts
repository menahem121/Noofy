import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  clearDashboardDraft: vi.fn(),
  removePendingImportedSetupReminder: vi.fn(),
  invalidateWorkflowUserStateCache: vi.fn(),
  invalidateWorkflowRunPageCache: vi.fn(),
}));

vi.mock("../dashboard-builder/dashboardBuilderContent", () => ({
  clearDashboardDraft: mocks.clearDashboardDraft,
}));
vi.mock("../home/pendingSetupBanners", () => ({
  removePendingImportedSetupReminder: mocks.removePendingImportedSetupReminder,
}));
vi.mock("../../lib/useWorkflowUserState", () => ({
  invalidateWorkflowUserStateCache: mocks.invalidateWorkflowUserStateCache,
}));
vi.mock("./workflowRunPageCache", () => ({
  invalidateWorkflowRunPageCache: mocks.invalidateWorkflowRunPageCache,
}));

import { cleanupRemovedWorkflowFrontendState } from "./workflowRemoval";

describe("cleanupRemovedWorkflowFrontendState", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("clears workflow drafts, reminders, caches, runtime state, and the open tab", () => {
    const closeWorkflowTab = vi.fn();

    cleanupRemovedWorkflowFrontendState("wf-deleted", { closeWorkflowTab });

    expect(mocks.clearDashboardDraft).toHaveBeenCalledWith("wf-deleted");
    expect(mocks.removePendingImportedSetupReminder).toHaveBeenCalledWith("wf-deleted");
    expect(mocks.invalidateWorkflowUserStateCache).toHaveBeenCalledWith("wf-deleted");
    expect(mocks.invalidateWorkflowRunPageCache).toHaveBeenCalledWith("wf-deleted");
    expect(closeWorkflowTab).toHaveBeenCalledWith("wf-deleted");
  });

  it("continues invalidating local state when one cleanup operation fails", () => {
    mocks.clearDashboardDraft.mockImplementationOnce(() => {
      throw new Error("storage unavailable");
    });
    const closeWorkflowTab = vi.fn();

    cleanupRemovedWorkflowFrontendState("wf-deleted", { closeWorkflowTab });

    expect(mocks.removePendingImportedSetupReminder).toHaveBeenCalledWith("wf-deleted");
    expect(mocks.invalidateWorkflowUserStateCache).toHaveBeenCalledWith("wf-deleted");
    expect(mocks.invalidateWorkflowRunPageCache).toHaveBeenCalledWith("wf-deleted");
    expect(closeWorkflowTab).toHaveBeenCalledWith("wf-deleted");
  });
});
