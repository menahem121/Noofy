import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  RequiredModelAvailability,
  RequiredModelSummary,
  WorkflowModelVerificationJobStatus,
  WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import { useGlobalModelReadiness } from "./useGlobalModelReadiness";
import {
  resetWorkflowModelSummaryListenersForTests,
  subscribeToWorkflowModelSummaryUpdates,
  type WorkflowModelSummaryUpdate,
} from "./workflowModelReadinessSync";
import {
  cachedWorkflowRunPageState,
  resetWorkflowRunPageCacheForTests,
  storeWorkflowRunPageState,
  type WorkflowRunPageCachedState,
} from "./workflowRunPageCache";

const workflowId = "text_to_image_v0";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const missingModel: RequiredModelAvailability = {
  requirement_id: "checkpoint",
  node_id: "4",
  node_type: "CheckpointLoaderSimple",
  input_name: "ckpt_name",
  filename: "v1-5-pruned-emaonly-fp16.safetensors",
  model_type: "Checkpoint",
  folder: "checkpoints",
  verification_level: "filename_only",
  size_bytes: null,
  source_urls: [],
  source_availability: "unknown",
  status: "missing",
  status_label: "Missing",
  asset_ownership: "community",
  source_path: null,
  matched_root: null,
  matched_sha256: null,
  matched_size_bytes: null,
  message: "Model is missing.",
  references: [],
  reference_count: 1,
  dedup_uncertain: false,
};

const missingSummary: RequiredModelSummary = {
  workflow_id: workflowId,
  total_count: 1,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 1,
  needs_manual_download_count: 0,
  ready_to_run: false,
  models: [missingModel],
};

const downloadedSummary: RequiredModelSummary = {
  ...missingSummary,
  available_count: 1,
  missing_count: 0,
  ready_to_run: true,
  models: [
    {
      ...missingModel,
      status: "available",
      status_label: "Available",
      source_path: "/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
      matched_root: "/models",
      message: null,
    },
  ],
};

const possibleMatchSummary: RequiredModelSummary = {
  ...missingSummary,
  possible_match_count: 1,
  missing_count: 0,
  models: [
    {
      ...missingModel,
      status: "possible_match",
      status_label: "Possible match",
      source_path: "/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
      matched_root: "/models",
      message: "A local file with this name was found, but Noofy needs stronger verification before using it.",
    },
  ],
};

const checkingSummary: RequiredModelSummary = {
  ...possibleMatchSummary,
  possible_match_count: 0,
  models: possibleMatchSummary.models.map((model) => ({
    ...model,
    status: "checking",
    status_label: "Checking",
    message: "Noofy is checking whether this model is already available locally.",
  })),
};

const missingModelValidation: WorkflowValidationResult = {
  workflow_id: workflowId,
  valid: false,
  missing_models: [
    {
      folder: "checkpoints",
      filename: "v1-5-pruned-emaonly-fp16.safetensors",
      source_url: null,
      checksum: null,
    },
  ],
  errors: [],
};

const staleCachedRunPageState: WorkflowRunPageCachedState = {
  firstLoadedWorkflowId: workflowId,
  workflowStatus: null,
  modelSummary: missingSummary,
  packageData: null,
  apiKeySettings: null,
  validation: missingModelValidation,
  modelSummaryLoading: false,
  validationLoading: false,
  job: null,
  progress: null,
  result: null,
  error: null,
  packageLoadError: null,
  packageLoadErrorStatus: null,
};

let readiness: ReturnType<typeof useGlobalModelReadiness>;

function Harness() {
  readiness = useGlobalModelReadiness();
  return <>{readiness.element}</>;
}

function completedDownloadJob(modelSummary: RequiredModelSummary) {
  return {
    job_id: "model-download-1",
    status: "completed",
    user_facing_message: "Model download check finished.",
    current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
    current_model_index: 1,
    total_models: 1,
    bytes_downloaded: 1024,
    total_bytes: 1024,
    percent: 100,
    speed_bytes_per_second: null,
    models: [],
    model_summary: modelSummary,
  };
}

function mockDownloadFlow(fetchMock: ReturnType<typeof vi.fn>, resultSummary: RequiredModelSummary) {
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/models/downloads") && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ job_id: "model-download-1", status: "queued", user_facing_message: "Downloading required models..." }),
      );
    }
    if (url.endsWith("/api/models/downloads/model-download-1")) {
      return Promise.resolve(jsonResponse(completedDownloadJob(resultSummary)));
    }
    if (url.endsWith(`/api/workflows/${workflowId}/model-summary`)) {
      return Promise.resolve(jsonResponse(resultSummary));
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
}

function verificationJob(
  status: WorkflowModelVerificationJobStatus["status"],
  modelSummary: RequiredModelSummary,
): WorkflowModelVerificationJobStatus {
  return {
    job_id: "workflow-model-verification-1",
    workflow_id: workflowId,
    status,
    user_facing_message: status === "completed" ? "Model verification finished." : "Model verification is queued.",
    current_model_filename: status === "completed" ? null : "v1-5-pruned-emaonly-fp16.safetensors",
    current_model_index: status === "completed" ? null : 1,
    total_models: 1,
    verified_models: status === "completed" ? 1 : 0,
    percent: status === "completed" ? 100 : 0,
    models: modelSummary.models,
    model_summary: modelSummary,
  };
}

describe("useGlobalModelReadiness", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    resetWorkflowModelSummaryListenersForTests();
    resetWorkflowRunPageCacheForTests();
  });

  it("publishes a ready summary without opening Missing Models", () => {
    storeWorkflowRunPageState(workflowId, staleCachedRunPageState);
    const updates: WorkflowModelSummaryUpdate[] = [];
    subscribeToWorkflowModelSummaryUpdates((update) => updates.push(update));

    render(<Harness />);
    act(() => {
      readiness.openMissingModels({ workflowId, workflowName: "Text to Image", summary: downloadedSummary });
    });

    expect(screen.queryByRole("dialog", { name: "Missing Models" })).not.toBeInTheDocument();
    expect(updates.some((update) => update.workflowId === workflowId && update.summary.ready_to_run)).toBe(true);

    const cached = cachedWorkflowRunPageState(workflowId, staleCachedRunPageState);
    expect(cached.modelSummary?.ready_to_run).toBe(true);
    expect(cached.validation).toBeNull();
  });

  it("keeps verification running in the background when the missing-model popup is closed", async () => {
    storeWorkflowRunPageState(workflowId, staleCachedRunPageState);
    const updates: WorkflowModelSummaryUpdate[] = [];
    subscribeToWorkflowModelSummaryUpdates((update) => updates.push(update));
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/api/workflows/${workflowId}/model-verification`) && init?.method === "POST") {
        return Promise.resolve(jsonResponse(verificationJob("queued", checkingSummary)));
      }
      if (url.endsWith(`/api/workflows/${workflowId}/model-verification/workflow-model-verification-1`)) {
        return Promise.resolve(jsonResponse(verificationJob("completed", downloadedSummary)));
      }
      if (url.endsWith(`/api/workflows/${workflowId}/model-summary`)) {
        return Promise.resolve(jsonResponse(downloadedSummary));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<Harness />);
    act(() => {
      readiness.openMissingModels({ workflowId, workflowName: "Text to Image", summary: possibleMatchSummary });
    });

    const dialog = await screen.findByRole("dialog", { name: "Missing Models" });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/workflows/${workflowId}/model-verification`,
        expect.objectContaining({ method: "POST" }),
      );
    });
    await within(dialog).findByRole("progressbar", { name: "Model verification progress" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Missing Models" })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(updates.some((update) => update.workflowId === workflowId && update.summary.ready_to_run)).toBe(true);
    }, { timeout: 2500 });

    const cached = cachedWorkflowRunPageState(workflowId, staleCachedRunPageState);
    expect(cached.modelSummary?.ready_to_run).toBe(true);
    expect(cached.validation).toBeNull();
  });

  it("publishes the refreshed summary and updates the cached run-page state after a successful download", async () => {
    storeWorkflowRunPageState(workflowId, staleCachedRunPageState);
    const updates: WorkflowModelSummaryUpdate[] = [];
    subscribeToWorkflowModelSummaryUpdates((update) => updates.push(update));
    mockDownloadFlow(fetchMock, downloadedSummary);

    render(<Harness />);
    act(() => {
      readiness.openMissingModels({ workflowId, workflowName: "Text to Image", summary: missingSummary });
    });

    const dialog = await screen.findByRole("dialog", { name: "Missing Models" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Download Missing Models" }));

    await waitFor(() => {
      expect(updates.some((update) => update.workflowId === workflowId && update.summary.ready_to_run)).toBe(true);
    });
    expect(await within(dialog).findByText("Text to Image has all required model files available.")).toBeInTheDocument();

    const cached = cachedWorkflowRunPageState(workflowId, staleCachedRunPageState);
    expect(cached.modelSummary?.ready_to_run).toBe(true);
    expect(cached.validation).toBeNull();
  });

  it("keeps missing-model state when the download finishes without making the workflow ready", async () => {
    storeWorkflowRunPageState(workflowId, staleCachedRunPageState);
    const updates: WorkflowModelSummaryUpdate[] = [];
    subscribeToWorkflowModelSummaryUpdates((update) => updates.push(update));
    mockDownloadFlow(fetchMock, missingSummary);

    render(<Harness />);
    act(() => {
      readiness.openMissingModels({ workflowId, workflowName: "Text to Image", summary: missingSummary });
    });

    const dialog = await screen.findByRole("dialog", { name: "Missing Models" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Download Missing Models" }));

    await waitFor(() => {
      expect(updates.some((update) => update.workflowId === workflowId)).toBe(true);
    });
    expect(updates.every((update) => !update.summary.ready_to_run)).toBe(true);

    const cached = cachedWorkflowRunPageState(workflowId, staleCachedRunPageState);
    expect(cached.modelSummary?.ready_to_run).toBe(false);
    expect(cached.validation).toEqual(missingModelValidation);
  });
});
