import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowLibraryProvider } from "../home/WorkflowLibraryProvider";
import { useWorkflowImportFlow, type WorkflowImportFlowController } from "./useWorkflowImportFlow";

const mocks = vi.hoisted(() => ({
  cleanupRemovedWorkflowFrontendState: vi.fn(),
}));

vi.mock("./workflowRemoval", () => ({
  cleanupRemovedWorkflowFrontendState: mocks.cleanupRemovedWorkflowFrontendState,
}));

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const duplicatePreview = {
  import_session_id: "duplicate-session",
  workflow_id: "publisher__portrait__1.0.0",
  status: "duplicate_identity",
  user_facing_message: "This workflow is already in Noofy.",
  workflow: {
    id: "publisher__portrait__1.0.0",
    name: "Portrait",
    version: "1.0.0",
    description: "",
    trust_level: "quarantined_community",
  },
  required_model_count: 0,
  custom_node_count: 0,
  unresolved_input_count: 0,
  model_summary: null,
  duplicate_identity: {
    status: "conflict",
    user_facing_message: "A workflow with this identity already exists in Noofy.",
    existing_workflow: {
      id: "publisher__portrait__1.0.0",
      name: "Portrait",
      version: "1.0.0",
    },
    incoming_workflow: {
      id: "publisher__portrait__1.0.0",
      name: "Portrait",
      version: "1.0.0",
    },
    actions: ["replace", "copy", "cancel"],
  },
};

function Harness({
  controllerRef,
  workflowTabs,
}: {
  controllerRef: { current: WorkflowImportFlowController | null };
  workflowTabs?: { closeWorkflowTab: (workflowId: string) => void };
}) {
  controllerRef.current = useWorkflowImportFlow({
    onOpenWorkflow: vi.fn(),
    workflowTabs,
  });
  return null;
}

describe("useWorkflowImportFlow", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    mocks.cleanupRemovedWorkflowFrontendState.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  async function stageDuplicateImport(controller: WorkflowImportFlowController) {
    await act(async () => {
      await controller.startWorkflowImport(new File(["archive"], "portrait.noofy"));
    });
  }

  it("invalidates stale frontend workflow state before opening a duplicate replacement", async () => {
    const workflowTabs = { closeWorkflowTab: vi.fn() };
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=portrait.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(duplicatePreview));
      }
      if (url.endsWith("/api/workflows/import/duplicate-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...duplicatePreview,
            import_session_id: null,
            status: "imported",
            user_facing_message: "Imported",
            duplicate_identity: null,
          }),
        );
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness controllerRef={controllerRef} workflowTabs={workflowTabs} />
      </WorkflowLibraryProvider>,
    );

    await stageDuplicateImport(controllerRef.current!);
    await waitFor(() => {
      expect(controllerRef.current?.state.pendingImport?.import_session_id).toBe("duplicate-session");
    });

    await act(async () => {
      await controllerRef.current!.duplicateImport("replace");
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/duplicate-session/commit", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ duplicate_action: "replace" }),
    });
    expect(mocks.cleanupRemovedWorkflowFrontendState).toHaveBeenCalledWith(
      "publisher__portrait__1.0.0",
      workflowTabs,
    );
  });

  it("does not invalidate existing workflow tabs when importing a duplicate as a copy", async () => {
    const workflowTabs = { closeWorkflowTab: vi.fn() };
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=portrait.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(duplicatePreview));
      }
      if (url.endsWith("/api/workflows/import/duplicate-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...duplicatePreview,
            import_session_id: null,
            workflow_id: "local__portrait-copy__1.0.0",
            status: "imported",
            user_facing_message: "Imported",
            workflow: {
              ...duplicatePreview.workflow,
              id: "local__portrait-copy__1.0.0",
              name: "Portrait Copy",
            },
            duplicate_identity: null,
          }),
        );
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness controllerRef={controllerRef} workflowTabs={workflowTabs} />
      </WorkflowLibraryProvider>,
    );

    await stageDuplicateImport(controllerRef.current!);
    await waitFor(() => {
      expect(controllerRef.current?.state.pendingImport?.import_session_id).toBe("duplicate-session");
    });

    await act(async () => {
      await controllerRef.current!.duplicateImport("copy");
    });

    expect(mocks.cleanupRemovedWorkflowFrontendState).not.toHaveBeenCalled();
  });
});
