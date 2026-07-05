import type {
  ApiKeySettingsResponse,
  EngineJob,
  JobProgress,
  JobResult,
  RequiredModelSummary,
  WorkflowPackageResponse,
  WorkflowStatusResponse,
  WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import { shouldClearMissingModelValidation } from "./workflowModelRequirements";

export interface WorkflowRunPageCachedState {
  firstLoadedWorkflowId: string | null;
  workflowStatus: WorkflowStatusResponse | null;
  modelSummary: RequiredModelSummary | null;
  packageData: WorkflowPackageResponse | null;
  apiKeySettings: ApiKeySettingsResponse | null;
  validation: WorkflowValidationResult | null;
  modelSummaryLoading: boolean;
  validationLoading: boolean;
  job: EngineJob | null;
  progress: JobProgress | null;
  result: JobResult | null;
  error: string | null;
  packageLoadError: string | null;
  packageLoadErrorStatus: number | null;
}

const workflowRunPageStateCache = new Map<string, WorkflowRunPageCachedState>();

export function cachedWorkflowRunPageState(
  workflowId: string,
  fallback: WorkflowRunPageCachedState,
): WorkflowRunPageCachedState {
  return workflowRunPageStateCache.get(workflowId) ?? fallback;
}

export function storeWorkflowRunPageState(workflowId: string, state: WorkflowRunPageCachedState) {
  workflowRunPageStateCache.set(workflowId, state);
}

export function invalidateWorkflowRunPageCache(workflowId: string) {
  workflowRunPageStateCache.delete(workflowId);
}

export function applyModelSummaryToWorkflowRunPageCache(
  workflowId: string,
  modelSummary: RequiredModelSummary,
) {
  const cached = workflowRunPageStateCache.get(workflowId);
  if (!cached) return;
  workflowRunPageStateCache.set(workflowId, {
    ...cached,
    modelSummary,
    modelSummaryLoading: false,
    validation: shouldClearMissingModelValidation(cached.validation, modelSummary)
      ? null
      : cached.validation,
  });
}

export function resetWorkflowRunPageCacheForTests() {
  workflowRunPageStateCache.clear();
}
