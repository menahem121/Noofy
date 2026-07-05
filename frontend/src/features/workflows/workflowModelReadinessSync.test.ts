import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequiredModelSummary, WorkflowValidationResult } from "../../lib/api/noofyApi";
import {
  publishWorkflowModelSummaryUpdate,
  resetWorkflowModelSummaryListenersForTests,
  subscribeToWorkflowModelSummaryUpdates,
} from "./workflowModelReadinessSync";
import {
  cachedWorkflowRunPageState,
  resetWorkflowRunPageCacheForTests,
  storeWorkflowRunPageState,
  type WorkflowRunPageCachedState,
} from "./workflowRunPageCache";

const workflowId = "text_to_image_v0";

const missingSummary: RequiredModelSummary = {
  workflow_id: workflowId,
  total_count: 1,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 1,
  needs_manual_download_count: 0,
  ready_to_run: false,
  models: [],
};

const readySummary: RequiredModelSummary = {
  ...missingSummary,
  available_count: 1,
  missing_count: 0,
  ready_to_run: true,
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

function cachedState(overrides: Partial<WorkflowRunPageCachedState> = {}): WorkflowRunPageCachedState {
  return {
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
    ...overrides,
  };
}

const fallbackState = cachedState({ firstLoadedWorkflowId: null, modelSummary: null, validation: null });

describe("workflowModelReadinessSync", () => {
  afterEach(() => {
    resetWorkflowModelSummaryListenersForTests();
    resetWorkflowRunPageCacheForTests();
    vi.clearAllMocks();
  });

  it("notifies subscribers and stops after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToWorkflowModelSummaryUpdates(listener);

    publishWorkflowModelSummaryUpdate({ workflowId, summary: readySummary });
    expect(listener).toHaveBeenCalledWith({ workflowId, summary: readySummary });

    unsubscribe();
    publishWorkflowModelSummaryUpdate({ workflowId, summary: missingSummary });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("updates cached run-page state and clears stale missing-model validation when the summary is ready", () => {
    storeWorkflowRunPageState(workflowId, cachedState());

    publishWorkflowModelSummaryUpdate({ workflowId, summary: readySummary });

    const cached = cachedWorkflowRunPageState(workflowId, fallbackState);
    expect(cached.modelSummary).toEqual(readySummary);
    expect(cached.validation).toBeNull();
  });

  it("keeps missing-model validation when the refreshed summary is still not ready", () => {
    storeWorkflowRunPageState(workflowId, cachedState());

    publishWorkflowModelSummaryUpdate({ workflowId, summary: missingSummary });

    const cached = cachedWorkflowRunPageState(workflowId, fallbackState);
    expect(cached.modelSummary).toEqual(missingSummary);
    expect(cached.validation).toEqual(missingModelValidation);
  });

  it("does not create a cache entry for a workflow that never loaded", () => {
    publishWorkflowModelSummaryUpdate({ workflowId, summary: readySummary });

    expect(cachedWorkflowRunPageState(workflowId, fallbackState)).toBe(fallbackState);
  });
});
