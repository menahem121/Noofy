import type { JobResult } from "../../lib/api/noofyApi";
import {
  cachedWorkflowRunPageState,
  storeWorkflowRunPageState,
  type WorkflowRunPageCachedState,
} from "./workflowRunPageCache";

type RunPageState = WorkflowRunPageCachedState;

const emptyRunPageState: RunPageState = {
  firstLoadedWorkflowId: null,
  workflowStatus: null,
  modelSummary: null,
  packageData: null,
  apiKeySettings: null,
  validation: null,
  modelSummaryLoading: false,
  validationLoading: false,
  job: null,
  progress: null,
  result: null,
  error: null,
  packageLoadError: null,
  packageLoadErrorStatus: null,
};

export function cacheRunPageResult(
  workflowId: string,
  current: RunPageState,
  result: JobResult,
  update: Partial<RunPageState> = {},
) {
  const next = { ...current, ...update, result };
  storeWorkflowRunPageState(workflowId, next);
  return next;
}

export function storeRunPageResultSnapshot(
  workflowId: string,
  result: JobResult,
  update: Partial<RunPageState> = {},
) {
  return cacheRunPageResult(
    workflowId,
    cachedWorkflowRunPageState(workflowId, emptyRunPageState),
    result,
    update,
  );
}
