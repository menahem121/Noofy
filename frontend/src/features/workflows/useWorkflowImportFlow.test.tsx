import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowLibraryProvider } from "../home/WorkflowLibraryProvider";
import {
  useWorkflowImportFlow,
  type BackgroundModelVerificationRequest,
  type WorkflowImportFlowController,
} from "./useWorkflowImportFlow";

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

const checkingModel = {
  requirement_id: "checkpoint",
  node_id: "1",
  node_type: "CheckpointLoaderSimple",
  input_name: "ckpt_name",
  filename: "missing.safetensors",
  model_type: "Checkpoint",
  folder: "checkpoints",
  verification_level: "sha256_size",
  size_bytes: 1024,
  source_urls: ["https://example.com/missing.safetensors"],
  source_availability: "known",
  status: "checking",
  status_label: "Checking",
  asset_ownership: "external_reference",
  source_path: null,
  matched_root: null,
  matched_sha256: null,
  matched_size_bytes: null,
  message: "Noofy is checking whether this model is already available locally.",
  references: [],
  reference_count: 1,
  dedup_uncertain: false,
};

const checkingModelPreview = {
  import_session_id: "checking-session",
  workflow_id: "publisher__missing-model__1.0.0",
  status: "imported",
  user_facing_message: "Ready to import",
  workflow: {
    id: "publisher__missing-model__1.0.0",
    name: "Missing Model",
    version: "1.0.0",
    description: "",
    trust_level: "quarantined_community",
  },
  required_model_count: 1,
  custom_node_count: 0,
  unresolved_input_count: 0,
  model_summary: {
    workflow_id: "publisher__missing-model__1.0.0",
    total_count: 1,
    available_count: 0,
    possible_match_count: 0,
    missing_count: 0,
    needs_manual_download_count: 0,
    ready_to_run: false,
    models: [checkingModel],
  },
  duplicate_identity: null,
};

function Harness({
  controllerRef,
  workflowTabs,
  onBackgroundModelVerificationNeeded,
}: {
  controllerRef: { current: WorkflowImportFlowController | null };
  workflowTabs?: { closeWorkflowTab: (workflowId: string) => void };
  onBackgroundModelVerificationNeeded?: (request: BackgroundModelVerificationRequest) => void;
}) {
  controllerRef.current = useWorkflowImportFlow({
    onOpenWorkflow: vi.fn(),
    workflowTabs,
    onBackgroundModelVerificationNeeded,
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

  it("commits immediately while all required models are still being verified locally", async () => {
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    const onBackgroundModelVerificationNeeded = vi.fn();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=missing.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(checkingModelPreview));
      }
      if (url.endsWith("/api/workflows/import/checking-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...checkingModelPreview,
            import_session_id: null,
            model_summary: null,
            user_facing_message: "Imported",
          }),
        );
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness
          controllerRef={controllerRef}
          onBackgroundModelVerificationNeeded={onBackgroundModelVerificationNeeded}
        />
      </WorkflowLibraryProvider>,
    );

    await act(async () => {
      await controllerRef.current!.startWorkflowImport(new File(["archive"], "missing.noofy"));
    });

    await waitFor(() => {
      expect(controllerRef.current?.state.importResult?.workflow_id).toBe("publisher__missing-model__1.0.0");
    });
    expect(controllerRef.current?.state.pendingImport).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/checking-session/commit", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: undefined,
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/workflows/import/checking-session/model-verification"),
      ),
    ).toBe(false);
    expect(onBackgroundModelVerificationNeeded).toHaveBeenCalledWith({
      workflowId: "publisher__missing-model__1.0.0",
      workflowName: "Missing Model",
      summary: checkingModelPreview.model_summary,
    });
  });

  it("commits immediately when required models are a mix of available and checking", async () => {
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    const onBackgroundModelVerificationNeeded = vi.fn();
    const mixedPreview = {
      ...checkingModelPreview,
      import_session_id: "mixed-session",
      required_model_count: 2,
      model_summary: {
        ...checkingModelPreview.model_summary,
        total_count: 2,
        available_count: 1,
        models: [
          {
            ...checkingModel,
            requirement_id: "available-checkpoint",
            filename: "available.safetensors",
            status: "available",
            status_label: "Available",
            source_path: "/models/checkpoints/available.safetensors",
          },
          checkingModel,
        ],
      },
    };
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=mixed.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(mixedPreview));
      }
      if (url.endsWith("/api/workflows/import/mixed-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...mixedPreview,
            import_session_id: null,
            model_summary: null,
            user_facing_message: "Imported",
          }),
        );
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness
          controllerRef={controllerRef}
          onBackgroundModelVerificationNeeded={onBackgroundModelVerificationNeeded}
        />
      </WorkflowLibraryProvider>,
    );

    await act(async () => {
      await controllerRef.current!.startWorkflowImport(new File(["archive"], "mixed.noofy"));
    });

    await waitFor(() => {
      expect(controllerRef.current?.state.importResult?.workflow_id).toBe("publisher__missing-model__1.0.0");
    });
    expect(controllerRef.current?.state.pendingImport).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/mixed-session/commit", expect.objectContaining({ method: "POST" }));
    expect(onBackgroundModelVerificationNeeded).toHaveBeenCalledWith({
      workflowId: "publisher__missing-model__1.0.0",
      workflowName: "Missing Model",
      summary: mixedPreview.model_summary,
    });
  });

  it("commits immediately when all required models are already available", async () => {
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    const onBackgroundModelVerificationNeeded = vi.fn();
    const availablePreview = {
      ...checkingModelPreview,
      import_session_id: "available-session",
      model_summary: {
        ...checkingModelPreview.model_summary,
        available_count: 1,
        ready_to_run: true,
        models: [
          {
            ...checkingModel,
            status: "available",
            status_label: "Available",
            source_path: "/models/checkpoints/missing.safetensors",
          },
        ],
      },
    };
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=available.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(availablePreview));
      }
      if (url.endsWith("/api/workflows/import/available-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...availablePreview,
            import_session_id: null,
            model_summary: null,
            user_facing_message: "Imported",
          }),
        );
      }
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness
          controllerRef={controllerRef}
          onBackgroundModelVerificationNeeded={onBackgroundModelVerificationNeeded}
        />
      </WorkflowLibraryProvider>,
    );

    await act(async () => {
      await controllerRef.current!.startWorkflowImport(new File(["archive"], "available.noofy"));
    });

    await waitFor(() => {
      expect(controllerRef.current?.state.importResult?.workflow_id).toBe("publisher__missing-model__1.0.0");
    });
    expect(controllerRef.current?.state.pendingImport).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/available-session/commit", expect.objectContaining({ method: "POST" }));
    expect(onBackgroundModelVerificationNeeded).not.toHaveBeenCalled();
  });

  it("keeps the import staged when any required model is already known non-ready", async () => {
    const controllerRef: { current: WorkflowImportFlowController | null } = { current: null };
    const missingPreview = {
      ...checkingModelPreview,
      import_session_id: "missing-session",
      model_summary: {
        ...checkingModelPreview.model_summary,
        missing_count: 1,
        models: [
          {
            ...checkingModel,
            status: "missing",
            status_label: "Missing",
            message: "Noofy can grab this model for you before the workflow runs.",
          },
        ],
      },
    };
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=missing.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(missingPreview));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(
      <WorkflowLibraryProvider>
        <Harness controllerRef={controllerRef} />
      </WorkflowLibraryProvider>,
    );

    await act(async () => {
      await controllerRef.current!.startWorkflowImport(new File(["archive"], "missing.noofy"));
    });

    await waitFor(() => {
      expect(controllerRef.current?.state.pendingImport?.import_session_id).toBe("missing-session");
    });
    expect(controllerRef.current?.state.pendingImport?.model_summary?.models[0].status).toBe("missing");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/workflows/import/missing-session/commit"),
      ),
    ).toBe(false);
  });
});
